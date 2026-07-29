"""
whatsapp_bot.py
Connects to WhatsApp via Neonize (unofficial multi-device client), listens
for messages in a group, decides whether the FAQ bot should respond, and
replies using rag.answer_question().

First run: a QR code will print in the terminal. Scan it with the WhatsApp
account you want the bot to run as (use a secondary/test number, not your
personal one).

Trigger logic (see decide_should_respond):
  1. Message starts with a keyword like "!faq" -> always respond.
  2. Message looks like a question ("?") -> respond only if the FAQ index
     has a confident match, otherwise stay silent.
  3. Otherwise -> ignore.

NOTE: Neonize's exact event/field names can change between versions -
check `neonize.events` in your installed version if this doesn't match
(https://github.com/krypton-byte/neonize).
"""

import logging
import os

from neonize.client import NewClient
from neonize.events import ConnectedEv, MessageEv, event

import rag

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("faq_bot")

TRIGGER_KEYWORDS = ("!faq", "!ask")

# Restrict the bot to specific group JIDs if you only want it active in
# one group. Leave empty to respond in every group/chat it's part of.
# Group JIDs look like "120363xxxxxxxxxx@g.us" - find yours by logging
# incoming message.info.message_source.chat once connected.
ALLOWED_GROUP_JIDS: set[str] = set()

# Defaults to a local file for easy local testing. In production, point
# this at the mounted persistent disk (see terraform/) so the WhatsApp
# session survives VM recreation, e.g.:
#   NEONIZE_DB_PATH=/mnt/bot-data/neonize.db
NEONIZE_DB_PATH = os.environ.get("NEONIZE_DB_PATH", "./neonize.db")

client = NewClient(name="faq-bot", database=NEONIZE_DB_PATH)


def decide_should_respond(text: str) -> bool:
    lowered = text.strip().lower()

    if any(lowered.startswith(kw) for kw in TRIGGER_KEYWORDS):
        return True

    if text.strip().endswith("?"):
        return rag.has_good_match(text)

    return False


def strip_trigger_keyword(text: str) -> str:
    lowered = text.strip().lower()
    for kw in TRIGGER_KEYWORDS:
        if lowered.startswith(kw):
            return text.strip()[len(kw):].strip()
    return text.strip()


@client.event(ConnectedEv)
def on_connected(client: NewClient, event_: ConnectedEv):
    log.info("Connected to WhatsApp. Bot is live.")


@client.event(MessageEv)
def on_message(client: NewClient, message: MessageEv):
    try:
        chat_jid = message.Info.MessageSource.Chat
        text = message.Message.conversation or ""
    except AttributeError:
        # Non-text message (image, sticker, etc.) - ignore.
        return

    if not text:
        return

    if ALLOWED_GROUP_JIDS and str(chat_jid) not in ALLOWED_GROUP_JIDS:
        return

    if not decide_should_respond(text):
        return

    question = strip_trigger_keyword(text)
    log.info("Answering question: %s", question)

    try:
        answer = rag.answer_question(question)
    except Exception:
        log.exception("Failed to generate answer")
        return

    client.send_message(chat_jid, text=answer)


if __name__ == "__main__":
    client.connect()
    event.wait()
