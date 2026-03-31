"""
Telegram Bot API integration.
"""

import re

import httpx
import structlog

from app.config import settings

logger = structlog.get_logger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org/bot"
MAX_MESSAGE_LENGTH = 4000


def extract_message_context(update: dict) -> dict | None:
    """
    Extract relevant fields from a Telegram update.
    Returns None if the update has no text message.
    """
    message = update.get("message") or update.get("edited_message")
    if not message:
        return None

    text = message.get("text")
    if not text:
        return None

    chat = message.get("chat", {})
    chat_id = chat.get("id")
    if not chat_id:
        return None

    user = message.get("from", {})
    user_id = str(user.get("id", ""))
    conversation_id = str(chat_id)
    message_id = message.get("message_id")

    return {
        "chat_id": chat_id,
        "text": text,
        "user_id": user_id,
        "conversation_id": conversation_id,
        "message_id": message_id,
    }


def _markdown_to_html(text: str) -> str:
    """
    Convert LLM Markdown to Telegram HTML (parse_mode=HTML).
    Order: escape HTML special chars first, then apply formatting.
    """
    # 1. Escape HTML special chars in raw content
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # 2. Headers: ### Title → <b>Title</b>
    text = re.sub(r"^#{1,6}\s*(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)

    # 3. Bold: **text** or __text__ → <b>text</b>
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)

    # 4. Code blocks: ```...``` → <code>...</code>
    text = re.sub(r"```[a-z]*\n?(.*?)```", r"<code>\1</code>", text, flags=re.DOTALL)
    # Inline code: `code` → <code>code</code>
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)

    # 6. Bullet points: "- item" or "* item" → "• item"
    text = re.sub(r"^[\*\-]\s+", "• ", text, flags=re.MULTILINE)

    # 7. Collapse 3+ consecutive newlines to 2
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def _split_message(text: str, max_len: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """Split long messages at paragraph boundaries."""
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        if len(current) + len(paragraph) + 2 <= max_len:
            current = (current + "\n\n" + paragraph).strip()
        else:
            if current:
                chunks.append(current)
            if len(paragraph) <= max_len:
                current = paragraph
            else:
                # Hard split paragraph
                while len(paragraph) > max_len:
                    chunks.append(paragraph[:max_len])
                    paragraph = paragraph[max_len:]
                current = paragraph
    if current:
        chunks.append(current)
    return chunks


_PAGINATION_PATTERN = re.compile(r"¿Querés ver todas\?", re.IGNORECASE)


async def send_message(
    chat_id: int | str,
    text: str,
    reply_to_message_id: int | None = None,
    keyboard_options: list[str] | None = None,
) -> None:
    """Send a message (potentially multi-chunk) to a Telegram chat."""
    if not settings.has_telegram:
        logger.warning("Telegram not configured, cannot send message")
        return

    # Convert Markdown to HTML for Telegram rendering
    html_text = _markdown_to_html(text)
    chunks = _split_message(html_text)
    url = f"{TELEGRAM_API_BASE}{settings.telegram_bot_token}/sendMessage"

    # Auto-show SI/NO keyboard when answer offers pagination
    if keyboard_options is None and _PAGINATION_PATTERN.search(text):
        keyboard_options = ["SI", "NO"]

    async with httpx.AsyncClient(timeout=15.0) as client:
        for i, chunk in enumerate(chunks):
            payload: dict = {
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": "HTML",
            }
            if i == 0 and reply_to_message_id:
                payload["reply_to_message_id"] = reply_to_message_id
            # Keyboard only on last chunk; only if there are options
            if i == len(chunks) - 1 and keyboard_options:
                payload["reply_markup"] = _build_keyboard(keyboard_options)

            try:
                response = await client.post(url, json=payload)
                if not response.is_success:
                    logger.error(
                        "Telegram API error sending message",
                        chat_id=chat_id,
                        chunk=i,
                        status=response.status_code,
                        body=response.text[:200],
                    )
            except httpx.HTTPError as exc:
                logger.error(
                    "Failed to send Telegram message",
                    chat_id=chat_id,
                    chunk=i,
                    error=str(exc),
                )


async def send_typing_action(chat_id: int | str) -> None:
    """Send typing indicator. Visible for ~5 seconds in Telegram."""
    if not settings.has_telegram:
        return
    url = f"{TELEGRAM_API_BASE}{settings.telegram_bot_token}/sendChatAction"
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            await client.post(url, json={"chat_id": chat_id, "action": "typing"})
        except httpx.HTTPError:
            pass  # Non-critical


def _build_keyboard(options: list[str]) -> dict:
    """
    Build a reply keyboard.
    - YES/NO: single row with two buttons.
    - Department list: one column, up to 8 options, with a 'hide keyboard' note.
    """
    if set(options) <= {"SI", "NO"}:
        return {
            "keyboard": [[{"text": "SI"}, {"text": "NO"}]],
            "one_time_keyboard": True,
            "resize_keyboard": True,
        }
    # Show up to 8 options in two columns
    buttons = options[:8]
    rows = []
    for i in range(0, len(buttons), 2):
        row = [{"text": buttons[i]}]
        if i + 1 < len(buttons):
            row.append({"text": buttons[i + 1]})
        rows.append(row)
    return {
        "keyboard": rows,
        "one_time_keyboard": True,
        "resize_keyboard": True,
    }


def is_reset_command(text: str) -> bool:
    return text.strip().lower() in {"/reset", "/borrar", "/start", "/borrar_memoria"}
