"""
find_group_jid.py
One-off helper to find the JID of a WhatsApp group (for ALLOWED_GROUP_JIDS
in whatsapp_bot.py). Uses the same session as the main bot - run this
after you've already scanned the QR code once via whatsapp_bot.py, so it
reconnects using the existing session rather than asking you to re-scan.

Tries two things:
  1. client.get_joined_groups() - instant, but known to sometimes miss
     groups depending on account/sync state (see neonize issue #25).
  2. Falls back to listening for incoming messages - send any message in
     the group you care about, and its JID will be printed. This always
     works, since it's reading directly off a real received message.

Usage:
    python find_group_jid.py
    (then, if step 1 doesn't show your group, send a message in it and
    watch for it to print here)
"""

import os

from dotenv import load_dotenv
from neonize.client import NewClient
from neonize.events import ConnectedEv, MessageEv, event
from neonize.utils import Jid2String

load_dotenv()

NEONIZE_DB_PATH = os.environ.get("NEONIZE_DB_PATH", "./neonize.db")
client = NewClient(NEONIZE_DB_PATH)


@client.event(ConnectedEv)
def on_connected(client: NewClient, event_: ConnectedEv):
    print("Connected. Trying get_joined_groups()...\n")
    try:
        groups = client.get_joined_groups()
        if not groups:
            print("No groups returned - this can happen even for accounts")
            print("with groups (see neonize issue #25). Falling back to")
            print("listening for messages instead.\n")
        for g in groups:
            print(f"{g.GroupName.Name!r:40} -> {Jid2String(g.JID)}")
    except Exception as e:
        print(f"get_joined_groups() failed ({e}), falling back to listening.\n")

    print("\nNow send any message in the group you want the JID for -")
    print("it'll print below as soon as it arrives. Ctrl+C to stop.\n")


@client.event(MessageEv)
def on_message(client: NewClient, message: MessageEv):
    try:
        chat_jid = message.Info.MessageSource.Chat
        text = message.Message.conversation or ""
    except AttributeError:
        return

    chat_jid_str = Jid2String(chat_jid)

    # Group JIDs end in @g.us; a 1:1 chat ends in @s.whatsapp.net - only
    # show groups, since that's what ALLOWED_GROUP_JIDS needs.
    if chat_jid_str.endswith("@g.us"):
        print(f"Group JID: {chat_jid_str}   (message: {text!r})")


if __name__ == "__main__":
    client.connect()
    event.wait()
