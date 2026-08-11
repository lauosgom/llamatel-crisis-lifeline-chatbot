"""
whatsapp_bot.py
Connects to WhatsApp via Neonize (unofficial multi-device client), listens
for messages in a group, decides whether the FAQ bot should respond, and
replies using rag.generate_answer().

First run: a QR code will print in the terminal. Scan it with the WhatsApp
account you want the bot to run as (use a secondary/test number, not your
personal one).

Trigger logic (see on_message):
  1. Message starts with a keyword like "!faq" -> always respond.
  2. Message looks like a question ("?") -> respond only if the FAQ index
     has a confident match, otherwise stay silent.
  3. Otherwise -> ignore (not evaluated, not logged).

Every message that reaches step 1 or 2 gets logged via rag.log_interaction,
whether or not the bot actually responds - unanswered questions are the
most useful signal for finding FAQ gaps later. Only the question text and
match outcome are stored, no sender/group identifying info.

Feedback: group members can react to a bot reply with an emoji (see
FEEDBACK_EMOJI) to rate it good/bad - see on_reaction. WhatsApp sends
reactions through the same message event as regular text, just with a
different field populated, so this is handled inside on_message rather
than a separate event handler. This complements the admin-side review
tool in review_logs.py.

NOTE: Neonize's exact event/field names can change between versions -
check `neonize.events` in your installed version if this doesn't match
(https://github.com/krypton-byte/neonize).
"""

import logging
import os

from dotenv import load_dotenv
from neonize.client import NewClient
from neonize.events import ConnectedEv, MessageEv, event
from neonize.utils import Jid2String

import rag

load_dotenv()

logging.basicConfig(level=logging.DEBUG)  # TEMP: verbose for troubleshooting - set back to INFO once resolved
log = logging.getLogger("faq_bot")

TRIGGER_KEYWORDS = ("!faq", "!ask")

# Reaction emoji -> feedback rating. Any reaction not in this map is
# ignored (people react with all sorts of emoji for reasons unrelated to
# rating an answer - only count the ones that clearly mean good/bad).
FEEDBACK_EMOJI = {
    "\U0001F44D": "good",  # 👍
    "\u2705": "good",  # ✅
    "\U0001F44E": "bad",  # 👎
    "\u274C": "bad",  # ❌
}

# Restrict the bot to specific group JIDs if you only want it active in
# one group. Leave empty to respond in every group/chat it's part of.
# Group JIDs look like "120363xxxxxxxxxx@g.us" - find yours with
# find_group_jid.py.
ALLOWED_GROUP_JIDS: set[str] = {"120363428376507286@g.us"}

# Defaults to a local file for easy local testing. In production, point
# this at the mounted persistent disk (see terraform/) so the WhatsApp
# session survives VM recreation, e.g.:
#   NEONIZE_DB_PATH=/mnt/bot-data/neonize.db
NEONIZE_DB_PATH = os.environ.get("NEONIZE_DB_PATH", "./neonize.db")

client = NewClient(NEONIZE_DB_PATH)

rag.ensure_query_log_table_exists()
rag.ensure_feedback_table_exists()


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
        reaction = message.Message.reactionMessage
        if reaction.key.ID:
            handle_reaction(reaction)
            return
        chat_jid = message.Info.MessageSource.Chat
        text = message.Message.conversation or ""
    except AttributeError:
        # Non-text message (image, sticker, etc.) - ignore.
        return

    if not text:
        return

    chat_jid_str = Jid2String(chat_jid)
    log.debug("Received message in %s: %r", chat_jid_str, text)

    if ALLOWED_GROUP_JIDS and chat_jid_str not in ALLOWED_GROUP_JIDS:
        log.debug("Ignored - %s not in ALLOWED_GROUP_JIDS", chat_jid_str)
        return

    stripped = text.strip()
    lowered = stripped.lower()
    is_keyword_triggered = any(lowered.startswith(kw) for kw in TRIGGER_KEYWORDS)
    looks_like_question = stripped.endswith("?")

    if not (is_keyword_triggered or looks_like_question):
        log.debug("Ignored - no trigger keyword and doesn't end in '?'")
        return  # not something the bot evaluates at all

    question = strip_trigger_keyword(text)

    try:
        matches = rag.retrieve(question)
    except Exception:
        log.exception("Retrieval failed")
        return

    top_distance = matches[0][3] if matches else None
    should_respond = is_keyword_triggered or (
        top_distance is not None and top_distance <= rag.DISTANCE_THRESHOLD
    )

    if not should_respond:
        log.info("No confident match, staying silent: %s", question)
        rag.log_interaction(question, responded=False, matches=matches)
        return

    log.info("Answering question: %s", question)

    try:
        answer = rag.generate_answer(question, matches)
    except Exception:
        log.exception("Failed to generate answer")
        return

    # Send first so we can capture the sent message's ID - logged with the
    # row, so a later reaction on this exact reply can be matched back to
    # this question (see handle_reaction).
    try:
        response = client.send_message(chat_jid, answer)
        sent_message_id = response.ID
    except Exception:
        log.exception("Failed to send message")
        sent_message_id = None

    rag.log_interaction(
        question, responded=True, answer=answer, matches=matches, message_id=sent_message_id
    )


def handle_reaction(reaction) -> None:
    """A group member reacted to some message. Only acts if the emoji is
    one of FEEDBACK_EMOJI and the reacted-to message matches a logged bot
    reply - anything else (reactions on other people's messages, emoji we
    don't interpret) is silently ignored."""
    emoji = reaction.text
    rating = FEEDBACK_EMOJI.get(emoji)
    if not rating:
        return  # not a feedback emoji we recognize, or reaction was removed (empty text)

    reacted_message_id = reaction.key.ID
    query_log_id = rag.find_query_log_id_by_message_id(reacted_message_id)
    if not query_log_id:
        return  # reaction wasn't on a bot reply we logged - nothing to attach it to

    log.info("Feedback via reaction: %s -> %s (query_log_id=%s)", emoji, rating, query_log_id)
    rag.record_feedback(query_log_id, source="reaction", rating=rating)


if __name__ == "__main__":
    client.connect()
    event.wait()
