import os
import json
import logging
import requests
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)

# ── Logging ──────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO
)
log = logging.getLogger(__name__)

TOKEN    = os.environ.get("TELEGRAM_TOKEN", "TU_TOKEN_AQUI")
API_BASE = os.environ.get("API_BASE", "http://api-server:4001")

USUARIOS_FILE = os.environ.get("USUARIOS_FILE", "usuarios.json")

ZONAS = [
    "GTRE01", "BRMO01", "BRMN01",
    "CBDR01",  "BRMF01",    "CRCO01",
    "QBOR01",  "BRMZI01", "TCYO01", "CRCGT01"
]

ZONAS_NOMBRES = {
    "GTRE01":     "Guatire",
    "BRMO01":     "Barquisimeto Oeste",
    "BRMN01":     "Barquisimeto Norte",
    "CBDR01":     "Cabudare",
    "BRMF01":     "Barquisimeto Fundalara",
    "CRCO01":     "Caricuao",
    "QBOR01":     "Quíbor",
    "BRMZI01":    "Barquisimeto Zona Industrial",
    "CRR01":      "Carora",
    "TCYO01":     "Tocuyo",
    "CRCGT01":    "Guarataro",
}

# ── Estados ───────────────────────────────────────────────────
# Buscar ONU
SELECCIONAR_ZONA_ONU = 0
ESPERANDO_SERIAL     = 1

# Buscar cédula
SELECCIONAR_ZONA_CED = 10
ESPERANDO_CEDULA     = 11

# Cambiar ciudad
SELECCIONAR_CIUDAD   = 20


# ╔══════════════════════════════════════════════════════════╗
# ║                    AUTORIZACION                         ║
# ╚══════════════════════════════════════════════════════════╝

def cargar_usuarios() -> list:
    try:
        with open(USUARIOS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            usuarios = data.get("usuarios", [])
            resultado = []
            for u in usuarios:
                if isinstance(u, str):
                    resultado.append({"username": u.strip().lstrip("@").lower(), "nombre": u})
                elif isinstance(u, dict):
                    resultado.append(u)
            return resultado
    except FileNotFoundError:
        log.warning(f"[AUTH] {USUARIOS_FILE} no encontrado")
        return []
    except Exception as e:
        log.error(f"[AUTH] Error leyendo {USUARIOS_FILE}: {e}")
        return []

def guardar_usuarios_lista(usuarios: list) -> bool:
    try:
        # Leer el archivo actual para preservar los admins
        try:
            with open(USUARIOS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}

        data["usuarios"] = usuarios  # solo sobreescribe usuarios, no admins

        with open(USUARIOS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        log.error(f"[AUTH] Error guardando {USUARIOS_FILE}: {e}")
        return False

def get_nombre(username: str) -> str:
    for u in cargar_usuarios():
        if u.get("username", "").lower() == username.lower():
            return u.get("nombre") or username
    return username

def autorizado(username) -> bool:
    if not username:
        return False
    usuarios = cargar_usuarios()
    log.info(f"[AUTH] verificando '{username}' contra {[u.get('username') for u in usuarios]}")
    return any(u.get("username", "").lower() == username.lower() for u in usuarios)

def es_admin(username) -> bool:
    if not username:
        return False
    try:
        with open(USUARIOS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            admins = [a.strip().lstrip("@").lower() for a in data.get("admins", [])]
            return username.lower() in admins
    except Exception:
        return False

async def rechazar_admin(update: Update) -> None:
    await update.message.reply_text(
        "🚫 *Acceso denegado*\n"
        "_Solo los administradores pueden usar este comando._",
        parse_mode="Markdown"
    )

async def rechazar(update: Update) -> None:
    user = update.effective_user
    log.warning(f"[AUTH] acceso denegado — id={user.id} username={user.username}")
    texto = (
        "🚫 *Acceso denegado*\n"
        f"Tu usuario de Telegram es: `@{user.username or 'sin username'}`\n\n"
        "_Contacta al administrador para solicitar acceso._"
    )
    if update.callback_query:
        await update.callback_query.answer("🚫 No tienes permiso", show_alert=True)
    elif update.message:
        await update.message.reply_text(texto, parse_mode="Markdown")


# ╔══════════════════════════════════════════════════════════╗
# ║                    HELPERS                              ║
# ╚══════════════════════════════════════════════════════════╝

def menu_principal():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📡 Buscar ONU",    callback_data="menu:onu"),
            InlineKeyboardButton("👤 Buscar Cédula", callback_data="menu:cedula"),
        ],
        [
            InlineKeyboardButton("🏙️ Cambiar ciudad", callback_data="menu:ciudad"),
            InlineKeyboardButton("📋 Ayuda",           callback_data="menu:ayuda"),
        ],
    ])

def botones_zonas(prefijo: str):
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(ZONAS_NOMBRES.get(zona, zona), callback_data=f"{prefijo}:{zona}")] for zona in ZONAS]
        + [[InlineKeyboardButton("❌ Salir", callback_data="salir")]]
    )

def boton_salir():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Salir", callback_data="salir")]])


# ╔══════════════════════════════════════════════════════════╗
# ║                    COMANDOS BASE                        ║
# ╚══════════════════════════════════════════════════════════╝

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    log.info(f"[/start] usuario={user.username or user.id}")

    if not autorizado(user.username):
        await rechazar(update)
        return

    nombre      = get_nombre(user.username)
    zona_act    = context.user_data.get("zona", None)
    primera_vez = not context.user_data.get("bienvenido", False)

    if primera_vez:
        context.user_data["bienvenido"] = True
        await update.message.reply_text(
            f"👋 Bienvenido, *{nombre}*\n\n"
            f"🤖 *Bot de Gestión de ONUs*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Para comenzar, selecciona tu ciudad de trabajo. "
            f"El bot la recordará para todas tus consultas.\n\n"
            f"📋 *Comandos disponibles:*\n\n"
            f"🏙️ /ciudad — Selecciona tu zona de trabajo\n"
            f"📡 /onu — Consulta valores ópticos por serial\n"
            f"👤 /cedula — Consulta un cliente por cédula\n"
            f"📋 /ayuda — Ver todos los comandos\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"_Selecciona tu ciudad para comenzar:_",
            parse_mode="Markdown"
        )
        await update.message.reply_text(
            "🏙️ ¿En qué zona vas a trabajar?",
            reply_markup=botones_zonas("ciudad")
        )
        return

    zona_info = f"🏙️ Ciudad activa: *{ZONAS_NOMBRES.get(zona_act, zona_act)}*" if zona_act else "⚠️ _Sin ciudad seleccionada. Usa_ /ciudad"
    await update.message.reply_text(
        f"👋 *{nombre}*\n"
        f"{zona_info}\n\n"
        f"¿Qué deseas hacer?",
        parse_mode="Markdown",
        reply_markup=menu_principal()
    )


async def ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not autorizado(user.username):
        await rechazar(update)
        return

    seccion_admin = (
        "\n👤 *Administración de usuarios:*\n\n"
        "/adduser @usuario Nombre — Agrega un usuario\n"
        "/deluser @usuario — Elimina un usuario\n"
        "/listusers — Lista los usuarios autorizados\n"
    ) if es_admin(user.username) else ""

    texto = (
        "🤖 *Bot de Gestión de ONUs*\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Para realizar cualquier acción, primero debes seleccionar tu ciudad con /ciudad. "
        "Una vez configurada, el bot la recordará para todas tus consultas.\n\n"
        "📋 *Comandos disponibles:*\n\n"
        "🏙️ /ciudad — Selecciona la zona de trabajo\n"
        "  _Debes elegirla antes de hacer consultas_\n\n"
        "📡 /onu — Consulta los valores ópticos de una ONU\n"
        "  _Puedes buscar por serial completo o parcial_\n"
        "  _Después de cada resultado puedes buscar otra_\n\n"
        "👤 /cedula — Consulta un cliente por cédula\n"
        "  _Muestra datos del contrato y estado óptico_\n\n"
        "📋 /ayuda — Muestra este mensaje\n"
        f"{seccion_admin}"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "_Ante cualquier problema usa_ /start _para volver al menú principal_"
    )

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            texto,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Volver", callback_data="menu:inicio")]])
        )
    else:
        await update.message.reply_text(texto, parse_mode="Markdown")


async def salir_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user   = update.effective_user
    nombre = get_nombre(user.username or "")
    zona   = context.user_data.get("zona", None)
    zona_nombre = ZONAS_NOMBRES.get(zona, zona) if zona else None
    zona_info   = f"🏙️ Ciudad activa: *{zona_nombre}*" if zona_nombre else "⚠️ _Sin ciudad seleccionada_"

    texto = (
        f"👋 *{nombre}*, hasta luego.\n"
        f"{zona_info}\n\n"
        f"Usa /start para volver al menú."
    )

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(texto, parse_mode="Markdown")
    elif update.message:
        await update.message.reply_text(texto, parse_mode="Markdown")

    return ConversationHandler.END


# ╔══════════════════════════════════════════════════════════╗
# ║                 FLUJO /ciudad                           ║
# ╚══════════════════════════════════════════════════════════╝

async def ciudad_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not autorizado(user.username):
        await rechazar(update)
        return ConversationHandler.END

    log.info(f"[/ciudad] usuario={user.username}")

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            "🏙️ Selecciona tu ciudad de trabajo:",
            reply_markup=botones_zonas("ciudad")
        )
    else:
        await update.message.reply_text(
            "🏙️ Selecciona tu ciudad de trabajo:",
            reply_markup=botones_zonas("ciudad")
        )
    return SELECCIONAR_CIUDAD


async def ciudad_seleccionada(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    zona = query.data.split(":")[1]
    context.user_data["zona"] = zona

    user   = update.effective_user
    nombre = get_nombre(user.username or "")
    log.info(f"[ciudad] usuario={user.username} — zona={zona}")

    await query.edit_message_text(
        f"✅ *{nombre}*, ya configuramos *{ZONAS_NOMBRES.get(zona, zona)}*.\n\n"
        f"¿Qué deseas hacer?",
        parse_mode="Markdown",
        reply_markup=menu_principal()
    )
    return ConversationHandler.END


# ╔══════════════════════════════════════════════════════════╗
# ║                 FLUJO /onu                              ║
# ╚══════════════════════════════════════════════════════════╝

async def onu_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not autorizado(user.username):
        await rechazar(update)
        return ConversationHandler.END

    zona = context.user_data.get("zona")

    # Si ya tiene zona, pedir serial directamente
    if zona:
        texto = (
            f"📡 Ciudad: *{zona}*\n\n"
            f"🔢 Escribe el serial de la ONU:\n"
            f"_Puede ser parcial, ej: B81ACD64_"
        )
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(
                texto, parse_mode="Markdown", reply_markup=boton_salir()
            )
        else:
            await update.message.reply_text(
                texto, parse_mode="Markdown", reply_markup=boton_salir()
            )
        return ESPERANDO_SERIAL

    # Si no tiene zona, pedir que seleccione
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            "⚠️ Primero debes seleccionar tu ciudad.\n\n"
            "🏙️ Selecciona la zona:",
            reply_markup=botones_zonas("zona_onu")
        )
    else:
        await update.message.reply_text(
            "⚠️ Primero debes seleccionar tu ciudad.\n\n"
            "🏙️ Selecciona la zona:",
            reply_markup=botones_zonas("zona_onu")
        )
    return SELECCIONAR_ZONA_ONU


async def zona_onu_seleccionada(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    zona = query.data.split(":")[1]
    context.user_data["zona"] = zona

    log.info(f"[zona_onu] usuario={update.effective_user.username} — zona={zona}")

    await query.edit_message_text(
        f"✅ Zona: *{zona}*\n\n"
        f"🔢 Escribe el serial de la ONU:\n"
        f"_Puede ser parcial, ej: B81ACD64_",
        parse_mode="Markdown",
        reply_markup=boton_salir()
    )
    return ESPERANDO_SERIAL


async def serial_recibido(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user   = update.effective_user
    if not autorizado(user.username):
        await rechazar(update)
        return ConversationHandler.END

    serial = update.message.text.strip().upper()
    zona   = context.user_data.get("zona")

    if not zona:
        await update.message.reply_text("⚠️ Sesión expirada. Usa /ciudad primero.")
        return ConversationHandler.END

    log.info(f"[serial] usuario={user.username} serial={serial} zona={zona}")

    msg = await update.message.reply_text(
        f"⏳ *Paso 1/3* — Conectando con servidor *{zona}*...",
        parse_mode="Markdown"
    )

    try:
        url = f"{API_BASE}/api/onu/buscar/{zona}/{serial}"
        log.info(f"[api] GET {url}")

        await msg.edit_text(
            f"⏳ *Paso 2/3* — Buscando serial `{serial}`...\n_Esto puede tardar unos segundos_",
            parse_mode="Markdown"
        )

        response = requests.get(url, timeout=60)
        log.info(f"[api] status={response.status_code} zona={zona} serial={serial}")

        await msg.edit_text(
            "⏳ *Paso 3/3* — Ejecutando diagnóstico en tiempo real...",
            parse_mode="Markdown"
        )

        data = response.json()

        if data.get("estado") != "OK":
            log.warning(f"[api] no encontrada — {data.get('mensaje')}")
            await msg.edit_text(
                f"⚠️ *{data.get('mensaje', 'No encontrada')}*\n"
                f"_{data.get('detalle', '')}_\n\n"
                f"🔢 Escribe otro serial o usa /start para salir:",
                parse_mode="Markdown",
                reply_markup=boton_salir()
            )
            return ESPERANDO_SERIAL

        clientes = data["resultado"]["clientes"]
        total    = data["resultado"].get("total", len(clientes))
        log.info(f"[api] encontrada — zona={zona} serial={serial} total={total}")

        await msg.delete()

        for i, cliente in enumerate(clientes):
            estado_onu = cliente.get("diagnostico", {}).get("onu", {}).get("onu_status", "—")
            log.info(f"[resultado] {i+1}/{total} nombre={cliente.get('nombre')} estado={estado_onu}")
            await update.message.reply_text(formatear_onu(cliente), parse_mode="Markdown")

        # Permitir otra consulta sin salir
        await update.message.reply_text(
            f"🔢 Escribe otro serial para consultar en *{zona}*\n"
            f"_O usa /ciudad para cambiar de zona_",
            parse_mode="Markdown",
            reply_markup=boton_salir()
        )
        return ESPERANDO_SERIAL

    except requests.exceptions.Timeout:
        log.error(f"[api] Timeout — zona={zona} serial={serial}")
        await msg.edit_text("❌ *Timeout* — La API tardó demasiado.\n_Intenta de nuevo._", parse_mode="Markdown")
    except requests.exceptions.ConnectionError:
        log.error(f"[api] ConnectionError — {API_BASE}")
        await msg.edit_text("❌ *Sin conexión* — No se pudo conectar con el servidor.", parse_mode="Markdown")
    except Exception as e:
        log.exception(f"[error] zona={zona} serial={serial} — {e}")
        await msg.edit_text(f"❌ *Error inesperado:*\n`{e}`", parse_mode="Markdown")

    return ESPERANDO_SERIAL


# ╔══════════════════════════════════════════════════════════╗
# ║                 FLUJO /cedula                           ║
# ╚══════════════════════════════════════════════════════════╝

async def cedula_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not autorizado(user.username):
        await rechazar(update)
        return ConversationHandler.END

    zona = context.user_data.get("zona")

    if zona:
        texto = (
            f"👤 Ciudad: *{zona}*\n\n"
            f"🪪 Escribe el número de cédula del cliente:"
        )
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(
                texto, parse_mode="Markdown", reply_markup=boton_salir()
            )
        else:
            await update.message.reply_text(
                texto, parse_mode="Markdown", reply_markup=boton_salir()
            )
        return ESPERANDO_CEDULA

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            "⚠️ Primero debes seleccionar tu ciudad.\n\n🏙️ Selecciona la zona:",
            reply_markup=botones_zonas("zona_ced")
        )
    else:
        await update.message.reply_text(
            "⚠️ Primero debes seleccionar tu ciudad.\n\n🏙️ Selecciona la zona:",
            reply_markup=botones_zonas("zona_ced")
        )
    return SELECCIONAR_ZONA_CED


async def zona_ced_seleccionada(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    zona = query.data.split(":")[1]
    context.user_data["zona"] = zona

    await query.edit_message_text(
        f"✅ Zona: *{zona}*\n\n🪪 Escribe el número de cédula del cliente:",
        parse_mode="Markdown",
        reply_markup=boton_salir()
    )
    return ESPERANDO_CEDULA


async def cedula_recibida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not autorizado(user.username):
        await rechazar(update)
        return ConversationHandler.END

    cedula = update.message.text.strip()
    zona   = context.user_data.get("zona")

    if not zona:
        await update.message.reply_text("⚠️ Sesión expirada. Usa /ciudad primero.")
        return ConversationHandler.END

    log.info(f"[cedula] usuario={user.username} cedula={cedula} zona={zona}")

    msg = await update.message.reply_text(
        f"⏳ *Buscando cédula* `{cedula}` *en* *{zona}*...\n_Esto puede tardar unos segundos_",
        parse_mode="Markdown"
    )

    try:
        url = f"{API_BASE}/api/clientes/cedula/{cedula}"
        log.info(f"[api] POST {url}")

        response = requests.post(url, json={"userId": 1}, timeout=60)
        log.info(f"[api] status={response.status_code} cedula={cedula}")

        data       = response.json()
        data_field = data.get("data") if isinstance(data, dict) else data

        if isinstance(data_field, dict):
            registros = [data_field]
        elif isinstance(data_field, list):
            registros = data_field
        else:
            registros = []

        if not registros:
            mensaje = data.get("message") if isinstance(data, dict) else "No encontrado"
            log.warning(f"[api] no encontrado — cedula={cedula}")
            await msg.edit_text(
                f"⚠️ *Cliente con cédula {cedula} no encontrado*\n_{mensaje}_\n\n"
                f"🪪 Escribe otra cédula o usa /start para salir:",
                parse_mode="Markdown",
                reply_markup=boton_salir()
            )
            return ESPERANDO_CEDULA

        mensajes_a_enviar = []
        for registro in registros:
            cliente           = registro.get("cliente", registro)
            wisphub           = cliente.get("wisphub", cliente)
            cliente815_lista  = cliente.get("cliente815") or []
            meta              = cliente.get("meta", {})

            if not cliente815_lista:
                mensajes_a_enviar.append(formatear_cliente(wisphub, None, meta))
            else:
                for c815 in cliente815_lista:
                    mensajes_a_enviar.append(formatear_cliente(wisphub, c815, meta))

        log.info(f"[api] encontrado — cedula={cedula} contratos={len(registros)} mensajes={len(mensajes_a_enviar)}")
        await msg.delete()

        for i, (texto, teclado) in enumerate(mensajes_a_enviar):
            log.info(f"[resultado] {i+1}/{len(mensajes_a_enviar)}")
            await update.message.reply_text(texto, parse_mode="Markdown", reply_markup=teclado)

        await update.message.reply_text(
            f"🪪 Escribe otra cédula para consultar en *{zona}*\n"
            f"_O usa /ciudad para cambiar de zona_",
            parse_mode="Markdown",
            reply_markup=boton_salir()
        )
        return ESPERANDO_CEDULA

    except requests.exceptions.Timeout:
        await msg.edit_text("❌ *Timeout* — La API tardó demasiado.\n_Intenta de nuevo._", parse_mode="Markdown")
    except requests.exceptions.ConnectionError:
        await msg.edit_text("❌ *Sin conexión* — No se pudo conectar con el servidor.", parse_mode="Markdown")
    except Exception as e:
        log.exception(f"[error] cedula={cedula} — {e}")
        await msg.edit_text(f"❌ *Error inesperado:*\n`{e}`", parse_mode="Markdown")

    return ESPERANDO_CEDULA


async def diagnostico_desde_cedula(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user  = update.effective_user

    if not autorizado(user.username):
        await rechazar(update)
        return

    await query.answer("🔍 Consultando diagnóstico...")

    _, zona, serial = query.data.split(":", 2)

    log.info(f"[diag_cli] usuario={user.username} zona={zona} serial={serial}")

    msg = await query.message.reply_text(
        f"⏳ *Paso 1/3* — Conectando con servidor *{zona}*...",
        parse_mode="Markdown"
    )

    try:
        url = f"{API_BASE}/api/onu/buscar/{zona}/{serial}"
        log.info(f"[api] GET {url}")

        await msg.edit_text(
            f"⏳ *Paso 2/3* — Buscando serial `{serial}`...\n_Esto puede tardar unos segundos_",
            parse_mode="Markdown"
        )

        response = requests.get(url, timeout=60)
        log.info(f"[api] status={response.status_code} zona={zona} serial={serial}")

        await msg.edit_text(
            "⏳ *Paso 3/3* — Ejecutando diagnóstico en tiempo real...",
            parse_mode="Markdown"
        )

        data = response.json()

        if data.get("estado") != "OK":
            log.warning(f"[api] no encontrada — {data.get('mensaje')}")
            await msg.edit_text(
                f"⚠️ *{data.get('mensaje', 'No encontrada')}*\n"
                f"_{data.get('detalle', '')}_",
                parse_mode="Markdown"
            )
            return

        clientes = data["resultado"]["clientes"]
        total    = data["resultado"].get("total", len(clientes))
        log.info(f"[api] encontrada — zona={zona} serial={serial} total={total}")

        await msg.delete()

        for i, cliente in enumerate(clientes):
            estado_onu = cliente.get("diagnostico", {}).get("onu", {}).get("onu_status", "—")
            log.info(f"[resultado] {i+1}/{total} nombre={cliente.get('nombre')} estado={estado_onu}")
            await query.message.reply_text(formatear_onu(cliente), parse_mode="Markdown")

    except requests.exceptions.Timeout:
        log.error(f"[api] Timeout — zona={zona} serial={serial}")
        await msg.edit_text("❌ *Timeout* — La API tardó demasiado.", parse_mode="Markdown")
    except requests.exceptions.ConnectionError:
        log.error(f"[api] ConnectionError — {API_BASE}")
        await msg.edit_text("❌ *Sin conexión* — No se pudo conectar con el servidor.", parse_mode="Markdown")
    except Exception as e:
        log.exception(f"[error] zona={zona} serial={serial} — {e}")
        await msg.edit_text(f"❌ *Error inesperado:*\n`{e}`", parse_mode="Markdown")
# ╔══════════════════════════════════════════════════════════╗
# ║                   MENU HANDLER                          ║
# ╚══════════════════════════════════════════════════════════╝

async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    user   = update.effective_user
    accion = query.data.split(":")[1]

    if not autorizado(user.username):
        await rechazar(update)
        return

    if accion == "ayuda":
        await ayuda(update, context)
    elif accion == "inicio":
        await query.answer()
        nombre   = get_nombre(user.username or "")
        zona_act = context.user_data.get("zona", None)
        zona_nombre_visual = ZONAS_NOMBRES.get(zona_act, zona_act) if zona_act else None
        zona_info = f"🏙️ Ciudad activa: *{zona_nombre_visual}*" if zona_nombre_visual else "⚠️ _Sin ciudad seleccionada_"
        await query.edit_message_text(
            f"👋 *{nombre}*\n{zona_info}\n\n¿Qué deseas hacer?",
            parse_mode="Markdown",
            reply_markup=menu_principal()
        )


# ╔══════════════════════════════════════════════════════════╗
# ║               ADMIN DE USUARIOS                         ║
# ╚══════════════════════════════════════════════════════════╝

async def adduser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not es_admin(user.username):
        await rechazar_admin(update)
        return

    if not context.args:
        await update.message.reply_text(
            "Uso: `/adduser @username Nombre Apellido`\n"
            "Ejemplo: `/adduser @juanperez Juan Pérez`",
            parse_mode="Markdown"
        )
        return

    nuevo_username = context.args[0].lstrip("@").lower().strip()
    nombre         = " ".join(context.args[1:]) if len(context.args) > 1 else nuevo_username

    usuarios = cargar_usuarios()
    if any(u.get("username", "").lower() == nuevo_username for u in usuarios):
        await update.message.reply_text(f"ℹ️ `@{nuevo_username}` ya está en la lista.", parse_mode="Markdown")
        return

    usuarios.append({"username": nuevo_username, "nombre": nombre})
    if guardar_usuarios_lista(usuarios):
        log.info(f"[AUTH] {user.username} agregó a @{nuevo_username} ({nombre})")
        await update.message.reply_text(
            f"✅ Usuario agregado:\n  👤 *{nombre}*\n  🏷️ `@{nuevo_username}`\n  👥 Total: {len(usuarios)}",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text("❌ Error al guardar.")


async def deluser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not es_admin(user.username):
        await rechazar_admin(update)
        return

    if not context.args:
        await update.message.reply_text("Uso: `/deluser @username`", parse_mode="Markdown")
        return

    objetivo = context.args[0].lstrip("@").lower().strip()
    if objetivo == (user.username or "").lower():
        await update.message.reply_text("⚠️ No puedes eliminarte a ti mismo.")
        return

    usuarios       = cargar_usuarios()
    nombre_elim    = get_nombre(objetivo)
    nuevos         = [u for u in usuarios if u.get("username", "").lower() != objetivo]

    if len(nuevos) == len(usuarios):
        await update.message.reply_text(f"ℹ️ `@{objetivo}` no está en la lista.", parse_mode="Markdown")
        return

    if guardar_usuarios_lista(nuevos):
        log.info(f"[AUTH] {user.username} eliminó a @{objetivo}")
        await update.message.reply_text(
            f"✅ Usuario eliminado:\n  👤 *{nombre_elim}*\n  🏷️ `@{objetivo}`\n  👥 Total: {len(nuevos)}",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text("❌ Error al guardar.")


async def listusers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not es_admin(user.username):
        await rechazar_admin(update)
        return

    usuarios = cargar_usuarios()
    if not usuarios:
        await update.message.reply_text("ℹ️ No hay usuarios autorizados.")
        return

    lista = "\n".join(
        f"  • *{u.get('nombre', u.get('username'))}* — `@{u.get('username')}`"
        for u in sorted(usuarios, key=lambda x: x.get("nombre", ""))
    )
    await update.message.reply_text(
        f"👥 *Usuarios autorizados ({len(usuarios)}):*\n{lista}",
        parse_mode="Markdown"
    )


# ╔══════════════════════════════════════════════════════════╗
# ║                    PARSERS                              ║
# ╚══════════════════════════════════════════════════════════╝

def parsear_ping(ping_data: str) -> dict:
    """
    Parsea output de PING y extrae tiempos de respuesta y estadísticas.
    Retorna dict con:
      - ping_times: lista de {sequence, time}
      - ping_summary: resumen estadístico
      - packet_loss: porcentaje de pérdida
      - es_exitoso: bool
      - rtt_avg: promedio de latencia
    """
    if not ping_data or not isinstance(ping_data, str):
        return {
            "ping_times": [],
            "ping_summary": "",
            "packet_loss": "—",
            "es_exitoso": False,
            "rtt_avg": "—"
        }
    
    ping_times = []
    
    # Extraer tiempos: "icmp_seq=1 ttl=64 time=1.23 ms"
    regex_tiempo = r'icmp_seq=(\d+).*?time=([\d.]+)\s*ms'
    matches = re.finditer(regex_tiempo, ping_data)
    
    for match in matches:
        ping_times.append({
            "sequence": int(match.group(1)),
            "time": float(match.group(2))
        })
    
    # Extraer resumen de estadísticas
    regex_summary = r'(\d+)\s+packets\s+transmitted.*?(\d+)\s+received.*?(\d+(?:\.\d+)?)\%\s+packet\s+loss'
    match_summary = re.search(regex_summary, ping_data)
    
    packet_loss = "—"
    es_exitoso = len(ping_times) > 0
    
    if match_summary:
        packet_loss = match_summary.group(3)
    
    # Extraer RTT avg
    rtt_avg = "—"
    regex_rtt = r'rtt\s+min/avg/max/[a-z]+\s*=\s*[\d.]+/([\d.]+)/([\d.]+)'
    match_rtt = re.search(regex_rtt, ping_data)
    if match_rtt:
        rtt_avg = match_rtt.group(1)
    
    # Resumen formateado
    ping_summary = ""
    if match_summary:
        transmitted = match_summary.group(1)
        received = match_summary.group(2)
        ping_summary = f"{transmitted} enviados, {received} recibidos, {packet_loss}% pérdida"
    
    return {
        "ping_times": ping_times,
        "ping_summary": ping_summary,
        "packet_loss": packet_loss,
        "es_exitoso": es_exitoso,
        "rtt_avg": rtt_avg
    }


# ╔══════════════════════════════════════════════════════════╗
# ║                    FORMATEADORES                        ║
# ╚══════════════════════════════════════════════════════════╝

def formatear_onu(c: dict) -> str:
    diag = c.get("diagnostico", {})
    olt  = diag.get("olt", {})
    onu  = diag.get("onu", {})
    conexion = diag.get("conexion", {})

    nombre      = c.get("nombre", "—")
    conector    = c.get("conector", "—")
    nodo        = c.get("nodo_de_red_815", "—")
    ciudad      = c.get("ciudad_815", "—")
    equipo      = c.get("equipo_cliente", "—")
    ip          = c.get("direccion_ip_815", {})
    ip_texto    = ip.get("direccion_ip", "—") if isinstance(ip, dict) else "—"
    status      = onu.get("onu_status", "—")
    serial_onu  = onu.get("onu_numero_de_serie", c.get("numero_de_serie", "—"))
    producto    = onu.get("onu_producto", "—")
    firmware    = onu.get("onu_firmware", "—")
    hardware    = onu.get("onu_hardware", "—")
    frame       = onu.get("frame_slot_pon_onu", "—")
    ip_onu      = onu.get("onu_direccion_ip", "—")
    mac_onu     = onu.get("onu_direccion_mac", "—")
    temperatura = onu.get("onu_temperatura", "—")
    voltaje     = onu.get("onu_voltaje", "—")
    onu_rx      = onu.get("onu_rx", "—")
    onu_tx      = onu.get("onu_tx", "—")
    olt_rx      = olt.get("pon_rx", "—")
    olt_tx      = olt.get("pon_tx", "—")

    # Parsear PING
    ping_data = conexion.get("conexion_ping_icmp", "")
    ping_result = parsear_ping(ping_data)
    dentro_de_cuota = conexion.get("dentro_de_cuota", False)

    es_offline           = "offline" in status.lower()
    icono                = "🔴" if es_offline else "🟢"
    status_detalle       = onu.get("onu_status_detalle", "—")
    status_detalle_desde = onu.get("onu_status_detalle_desde", "—")
    linea_offline        = (f"\n⚠️ *Motivo:* {status_detalle}\n🕐 *Desde:* {status_detalle_desde}") if es_offline else ""

    # Sección de conectividad
    seccion_ping = ""
    if ping_result["es_exitoso"]:
        seccion_ping = (
            f"\n🌐 *Conectividad PING*\n"
            f"  {ping_result['ping_summary']}\n"
            f"  RTT promedio: {ping_result['rtt_avg']} ms\n"
        )
    elif ping_data:
        seccion_ping = (
            f"\n🌐 *Conectividad PING*\n"
            f"  ❌ Sin respuesta — {ping_result['packet_loss']}% pérdida\n"
        )
    
    cuota_info = f"  📊 Dentro de cuota: {'✅ Sí' if dentro_de_cuota else '❌ No'}\n" if conexion else ""

    return (
        f"{icono} *{nombre}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔢 *Serial:* `{serial_onu}`\n"
        f"🏷️ *Conector:* {conector}\n"
        f"🌐 *IP:* {ip_texto}\n"
        f"🏙️ *Ciudad:* {ciudad}\n"
        f"📡 *Nodo:* {nodo}\n"
        f"🖥️ *Equipo:* {equipo}\n\n"
        f"📦 *Dispositivo*\n"
        f"  Modelo: {producto}  |  FW: {firmware}  |  HW: {hardware}\n"
        f"  Frame-Slot-PON-ONU: `{frame}`\n"
        f"  IP ONU: {ip_onu}\n"
        f"  MAC: `{mac_onu}`\n\n"
        f"📊 *Óptica*\n"
        f"  ONU RX: `{onu_rx}`   TX: `{onu_tx}`\n"
        f"  OLT RX: `{olt_rx}`   TX: `{olt_tx}`\n\n"
        f"🌡️ Temp: {temperatura}   ⚡ Voltaje: {voltaje}\n"
        f"{seccion_ping}"
        f"{cuota_info}"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Estado: {icono} *{status}*{linea_offline}"
    )


def formatear_cliente(wisphub: dict, c815, meta: dict = None):
    meta = meta or {}

    nombre         = wisphub.get("nombre", "—")
    conector       = wisphub.get("servicio") or str(wisphub.get("id_servicio", "—"))
    cedula         = wisphub.get("cedula", "—")
    domicilio      = wisphub.get("direccion", "—")
    telefono       = wisphub.get("telefono", "—")
    email          = wisphub.get("email", "—")
    zona           = wisphub.get("zona", {})
    zona_nombre    = zona.get("nombre", "—") if isinstance(zona, dict) else str(zona)
    fecha_alta     = wisphub.get("fecha_instalacion", "—")
    fecha_corte    = wisphub.get("fecha_corte", "—")
    estado_factura = wisphub.get("estado_facturas", "—")
    plan           = wisphub.get("plan_internet", {})
    plan_nombre    = plan.get("nombre", "—") if isinstance(plan, dict) else str(plan)
    estado_wisphub = wisphub.get("estado", "—")

    if c815:
        activa        = c815.get("activa", None)
        serial        = c815.get("numero_de_serie", "—")
        mac           = c815.get("direccion_mac", "—")
        estado_optico = c815.get("estado_optico", "—")
        onu_rx        = c815.get("ultimo_rx_power_onu", "—")
        onu_tx        = c815.get("ultimo_tx_power_onu", "—")
        olt_rx        = c815.get("ultimo_rx_power_puerto", "—")
        olt_tx        = c815.get("ultimo_tx_power_puerto", "—")
        zona_815      = c815.get("ciudad_815")
    else:
        activa        = None
        serial        = "—"
        mac           = "—"
        estado_optico = "—"
        onu_rx = onu_tx = olt_rx = olt_tx = "—"
        zona_815      = None

    estado_815   = "✅ Activo" if activa is True else ("❌ Inactivo" if activa is False else "— Desconocido")
    es_offline   = "offline" in str(estado_optico).lower()
    icono_optico = "🔴" if es_offline else ("🟢" if c815 else "⚪")

    aviso_815 = ""
    if not c815:
        warning = meta.get("warning", "Sin datos en 815")
        aviso_815 = f"\n⚠️ _{warning}_"

    texto = (
        f"👤 *{nombre}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🪪 *Cédula:* {cedula}\n"
        f"📍 *Domicilio:* {domicilio}\n"
        f"📞 *Teléfono:* {telefono}\n"
        f"📧 *Email:* {email}\n\n"
        f"📋 *Contrato*\n"
        f"  Zona: {zona_nombre}\n"
        f"  Contrato: {conector}\n"
        f"  Plan: {plan_nombre}\n"
        f"  Instalación: {fecha_alta}\n"
        f"  Fecha de corte: {fecha_corte}\n\n"
        f"📊 *Estados*\n"
        f"  Contrato: {estado_wisphub}\n"
        f"  815: {estado_815}\n"
        f"  Facturas: {estado_factura}\n"
        f"{aviso_815}\n\n"
        f"📡 *Información de la ONU*\n"
        f"  Serial: `{serial}`\n"
        f"  MAC: `{mac}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Estado de la ONU: {icono_optico} *{estado_optico}*"
    )

    teclado = None
    if c815 and zona_815 and serial and serial != "—":
        teclado = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔍 Ver diagnóstico ONU", callback_data=f"diag_cli:{zona_815}:{serial}")],
            [InlineKeyboardButton("❌ Salir", callback_data="salir")],
        ])

    return texto, teclado
# ╔══════════════════════════════════════════════════════════╗
# ║                       MAIN                              ║
# ╚══════════════════════════════════════════════════════════╝

def main():
    log.info(f"🤖 Iniciando bot — API_BASE={API_BASE}")
    log.info(f"👥 Usuarios: {[u.get('username') for u in cargar_usuarios()]}")

    app = Application.builder().token(TOKEN).build()

    # ── Conversation: /ciudad ────────────────────────────────
    conv_ciudad = ConversationHandler(
        entry_points=[
            CommandHandler("ciudad", ciudad_start),
            CallbackQueryHandler(ciudad_start, pattern=r"^menu:ciudad$"),
        ],
        states={
            SELECCIONAR_CIUDAD: [CallbackQueryHandler(ciudad_seleccionada, pattern=r"^ciudad:")],
        },
        fallbacks=[
            CommandHandler("start", start),
            CallbackQueryHandler(salir_handler, pattern=r"^salir$"),
        ],
    )

    # ── Conversation: /onu ───────────────────────────────────
    conv_onu = ConversationHandler(
        entry_points=[
            CommandHandler("onu", onu_start),
            CallbackQueryHandler(onu_start, pattern=r"^menu:onu$"),
        ],
        states={
            SELECCIONAR_ZONA_ONU: [CallbackQueryHandler(zona_onu_seleccionada, pattern=r"^zona_onu:")],
            ESPERANDO_SERIAL:     [MessageHandler(filters.TEXT & ~filters.COMMAND, serial_recibido)],
        },
        fallbacks=[
            CommandHandler("start",  start),
            CommandHandler("salir",  salir_handler),
            CommandHandler("ciudad", ciudad_start),
            CommandHandler("cedula", cedula_start),
            CallbackQueryHandler(cedula_start,  pattern=r"^menu:cedula$"),
            CallbackQueryHandler(ciudad_start,  pattern=r"^menu:ciudad$"),
            CallbackQueryHandler(salir_handler, pattern=r"^salir$"),
        ],
        allow_reentry=True,
    )

    # ── Conversation: /cedula ────────────────────────────────
    conv_cedula = ConversationHandler(
        entry_points=[
            CommandHandler("cedula", cedula_start),
            CallbackQueryHandler(cedula_start, pattern=r"^menu:cedula$"),
        ],
        states={
            SELECCIONAR_ZONA_CED: [CallbackQueryHandler(zona_ced_seleccionada, pattern=r"^zona_ced:")],
            ESPERANDO_CEDULA:     [MessageHandler(filters.TEXT & ~filters.COMMAND, cedula_recibida)],
        },
        fallbacks=[
            CommandHandler("start",  start),
            CommandHandler("salir",  salir_handler),
            CommandHandler("ciudad", ciudad_start),
            CommandHandler("onu",    onu_start),
            CallbackQueryHandler(onu_start,     pattern=r"^menu:onu$"),
            CallbackQueryHandler(ciudad_start,  pattern=r"^menu:ciudad$"),
            CallbackQueryHandler(salir_handler, pattern=r"^salir$"),
        ],
        allow_reentry=True,
    )

    # ── Handler global para ciudad: desde /start ─────────────
    app.add_handler(CallbackQueryHandler(ciudad_seleccionada, pattern=r"^ciudad:"))
    app.add_handler(CallbackQueryHandler(diagnostico_desde_cedula, pattern=r"^diag_cli:"))

    # ── Conversations primero para que capturen menu:* ────────
    app.add_handler(conv_ciudad)
    app.add_handler(conv_onu)
    app.add_handler(conv_cedula)

    # ── Comandos y handlers generales ────────────────────────
    app.add_handler(CommandHandler("start",     start))
    app.add_handler(CommandHandler("ayuda",     ayuda))
    app.add_handler(CommandHandler("salir",     salir_handler))
    app.add_handler(CommandHandler("adduser",   adduser))
    app.add_handler(CommandHandler("deluser",   deluser))
    app.add_handler(CommandHandler("listusers", listusers))

    # ── menu_handler solo para ayuda e inicio (después de convs)
    app.add_handler(CallbackQueryHandler(menu_handler, pattern=r"^menu:(ayuda|inicio)$"))

    log.info("✅ Bot listo, esperando mensajes...")
    app.run_polling()


if __name__ == "__main__":
    main()