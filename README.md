# WhatsApp FAQ Bot (RAG on BigQuery)

Answers frequently asked questions in a WhatsApp group using retrieval-augmented
generation over your own FAQ document, with the vector store hosted in
Google BigQuery.

## How it works

```
faq_data.json --build_index.py--> BigQuery table (id, question, answer, embedding)

WhatsApp group message (via Neonize)
  -> rag.retrieve()            [embed question -> BigQuery VECTOR_SEARCH, once]
  -> keyword trigger, or "?" + confident match -> decide whether to respond
  -> rag.log_interaction()     [logs the question + outcome either way - see Query logging]
  -> if responding: rag.generate_answer()   [GPT-4o-mini, using the same retrieved matches]
  -> reply sent back to the group
```

A note on the choice: BigQuery is an analytics warehouse, not a low-latency
transactional database, so expect query times in the range of a few hundred
milliseconds to a couple of seconds per lookup (not the sub-100ms you'd get
from a purpose-built vector DB like Pinecone). At your FAQ dataset's size,
this is a fine trade-off if you want everything living in GCP/SQL rather
than another service to manage.

## Setup

All infrastructure (BigQuery dataset/table, the bot VM, disk, firewall) is
managed by Terraform — see **Infrastructure (Terraform)** below for the
full walkthrough, including creating the service account and granting it
access. Come back here once that's done and you have a service account key.

All secrets and config (`OPENAI_API_KEY`, `GCP_PROJECT_ID`,
`GOOGLE_APPLICATION_CREDENTIALS`, etc.) live in a `.env` file rather than
being exported manually or hardcoded. Copy the template and fill it in:
```bash
cp .env.example .env
```
Then edit `.env` with your real values. It's already gitignored, so it
won't end up committed.

## Running the bot

1. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

2. Put your FAQ content in `faq_data.json`, following this schema:
   ```json
   [
     {"question": "...", "answer": "..."},
     {"question": "...", "answer": "..."}
   ]
   ```
   (A sample file is included — replace it with your real data.)

3. Build the index — this embeds each FAQ entry and loads it into
   BigQuery. `build_index.py` creates the table automatically on first
   run, and truncates + reloads it on every subsequent run, so it's safe
   to re-run whenever the FAQ doc changes:
   ```
   python build_index.py
   ```

4. Test the RAG pipeline on its own, no WhatsApp needed:
   ```
   python rag.py "How do I reset my password?"
   ```

5. Start the WhatsApp bot:
   ```
   python whatsapp_bot.py
   ```
   Scan the QR code that prints in your terminal with the WhatsApp account
   you want the bot to run as. **Use a secondary/test number**, not your
   personal one — Neonize uses WhatsApp's unofficial multi-device protocol
   (the same one behind tools like Baileys), which is not sanctioned by
   Meta and carries some risk of the number being flagged if used heavily
   or aggressively.

6. Add that WhatsApp number to your target group. It will now listen for
   messages there.

## Tuning

- `whatsapp_bot.py` → `TRIGGER_KEYWORDS`: change the keyword(s) that always
  trigger a response (e.g. `!faq`, `@bot`).
- `whatsapp_bot.py` → `ALLOWED_GROUP_JIDS`: restrict the bot to specific
  groups. Leave empty to allow all groups the account is in.
- `rag.py` → `DISTANCE_THRESHOLD`: controls how confident a match must be
  before the bot answers an untriggered "?" message. This uses BigQuery's
  COSINE distance (0 = identical, 2 = opposite), so LOWER = stricter
  matching. Print the distance from `retrieve()` against a few real
  questions to calibrate this for your data.
- `rag.py` → `CHAT_MODEL`: swap in `gpt-4o` for higher quality at higher
  cost, or keep `gpt-4o-mini` for a cheap/fast FAQ bot.

## Query logging

Every question that reaches the bot's trigger logic — whether it gets a
confident match and a reply, or comes up empty and stays silent — is
logged to a separate BigQuery table (`query_logs` by default) via
`rag.log_interaction()`. The goal is to build up a real-world question
log you can periodically review to find gaps in the FAQ and enrich it
over time.

What's stored per row: a random `id`, `timestamp`, the `question` text,
the `matched_faq_id` and `distance` of the best match (if any), whether
the bot `responded`, and the `answer` text if it did. **No sender or group
identifying info is captured** - just the question and outcome.

The table is created automatically the first time the bot runs
(`ensure_query_log_table_exists()`), partitioned by day for cheap querying
later. It's also declared in `terraform/main.tf` if you'd rather Terraform
own its lifecycle instead of the bot process creating it on first run -
either way ends up with the same schema.

A couple of useful starting queries once you've got some real traffic:

```sql
-- Questions that came up with no confident match - the best signal for
-- what's missing from your FAQ
SELECT question, distance, timestamp
FROM `YOUR_PROJECT_ID.faq_bot.query_logs`
WHERE responded = FALSE
ORDER BY timestamp DESC;

-- Which FAQ entries get asked about most, to prioritize what to refine
SELECT matched_faq_id, COUNT(*) AS times_asked
FROM `YOUR_PROJECT_ID.faq_bot.query_logs`
WHERE responded = TRUE
GROUP BY matched_faq_id
ORDER BY times_asked DESC;
```

Logging failures are swallowed inside `log_interaction()` - a BigQuery
hiccup will never block or break the bot's actual reply to the group.

## Scaling up later

If your FAQ table grows past a few thousand rows, add a vector index to
keep `VECTOR_SEARCH` fast:
```sql
CREATE VECTOR INDEX faq_embedding_idx
ON `YOUR_PROJECT_ID.faq_bot.faq_embeddings`(embedding)
OPTIONS(index_type = 'IVF', distance_type = 'COSINE');
```
Below a few thousand rows this doesn't help (and BigQuery may just ignore
it and fall back to brute-force search), so skip it until you actually
need it.

## Infrastructure (Terraform)

The `terraform/` folder provisions:
- The BigQuery dataset + table (matching the schema `build_index.py` expects)
- A dedicated `e2-micro` Compute Engine VM with **no public IP** — reachable
  only via IAP SSH tunneling — plus a separate persistent disk so the
  WhatsApp session (`neonize.db`) survives VM recreation
- A firewall rule allowing SSH only from Google's IAP range
- A key for the bot's service account (see the note on keys below)

It does **not** create the service account itself or grant it any IAM
roles — both of those are done manually (steps 1 and 3 below), by design,
so you can reuse an existing service account and control exactly what
access it gets.

### 1. Create the service account (manual)

**Console:** IAM & Admin → Service Accounts → **+ Create Service Account**.
Give it a name like `faq-bot-sa`, and skip the "grant access" step when
creating it — roles are added separately in step 3.

**Or via gcloud:**
```bash
gcloud iam service-accounts create faq-bot-sa \
  --display-name="WhatsApp FAQ bot service account" \
  --project=YOUR_PROJECT_ID
```

If you're reusing an existing service account (e.g. one already used by
another pipeline in this project), you can skip this step — just use its
email in step 2.

### 2. Apply the Terraform config

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars: set project_id and bot_service_account_email
# (the account from step 1, or an existing one you're reusing)

terraform init
terraform plan
terraform apply
```

### 3. Grant the service account access (manual)

Two roles are needed — one project-level, one dataset-level:

```bash
# Project-level: required for running BigQuery query jobs at all
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:faq-bot-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/bigquery.jobUser"

# Dataset-level: scoped to just this dataset, not project-wide
bq add-iam-policy-binding \
  --member="serviceAccount:faq-bot-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/bigquery.dataEditor" \
  YOUR_PROJECT_ID:faq_bot
```

Skip either grant if the account already has it (or a broader role like
`roles/editor`) from other uses in this project — check **IAM & Admin →
IAM** first.

### 4. Get your credentials out of state

```bash
terraform output -raw service_account_key_base64 | base64 -d > ../faq-bot-key.json
```
⚠️ This key resource writes private key material into Terraform state.
Treat `terraform.tfstate` as a secret from this point on — don't commit it,
and use a remote backend (see the commented-out `backend "gcs"` block in
`versions.tf`) rather than leaving it on a laptop. If you already have a
key for this account from its other uses, or would rather avoid Terraform
handling key material at all, delete the `google_service_account_key`
resource from `main.tf` and create the key manually instead:
```bash
gcloud iam service-accounts keys create faq-bot-key.json \
  --iam-account=faq-bot-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com
```

### 5. Deploy the bot onto the VM

```bash
# SSH in (no public IP, so this tunnels through IAP)
$(terraform output -raw ssh_command)

# From your machine, in a separate terminal, copy the project files over:
gcloud compute scp --recurse --zone=<your-zone> \
  ../*.py ../*.json ../requirements.txt ../.env.example faq-bot-key.json \
  <vm-name>:/opt/whatsapp-faq-bot/ --tunnel-through-iap
```

Then, on the VM:
```bash
cd /opt/whatsapp-faq-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
nano .env   # fill in OPENAI_API_KEY, GCP_PROJECT_ID, etc. - see below
```

Set these values in `.env`:
```bash
OPENAI_API_KEY=sk-...
GCP_PROJECT_ID=<your-project-id>
BQ_DATASET_ID=faq_bot                                  # optional, this is the default
BQ_TABLE_ID=faq_embeddings                             # optional, this is the default
GOOGLE_APPLICATION_CREDENTIALS=/opt/whatsapp-faq-bot/faq-bot-key.json
NEONIZE_DB_PATH=/mnt/bot-data/neonize.db               # the persistent disk Terraform attached
```

Then run it:
```bash
python build_index.py         # loads faq_data.json into BigQuery
python whatsapp_bot.py         # scan the QR code - first run only, do this interactively
```

### 6. Run it as a service

Once you've scanned the QR code once and confirmed it connects, wire it up
as a systemd service so it survives reboots and restarts on crash. A
starter unit file is at `terraform/whatsapp-faq-bot.service` — it already
points at `/opt/whatsapp-faq-bot/.env` as its `EnvironmentFile`, so the
`.env` you just created is reused directly, no extra setup needed:
```bash
sudo cp whatsapp-faq-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now whatsapp-faq-bot
```

## Notes

- Neonize session data lives in `./neonize.db` — deleting it will require
  re-scanning the QR code.
- Costs: each triggered message uses 1 OpenAI embedding call, 1 BigQuery
  query (billed by bytes scanned — negligible at this table size), and 1
  OpenAI chat completion call. All told, fractions of a cent per question.