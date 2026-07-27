# WhatsApp FAQ Bot (RAG on BigQuery)

Answers frequently asked questions in a WhatsApp group using retrieval-augmented
generation over your own FAQ document, with the vector store hosted in
Google BigQuery.

## How it works

```
faq_data.json --build_index.py--> BigQuery table (id, question, answer, embedding)

WhatsApp group message (via Neonize)
  -> decide_should_respond()   [keyword trigger, or "?" + confident match]
  -> rag.answer_question()     [embed question -> BigQuery VECTOR_SEARCH -> GPT-4o-mini]
  -> reply sent back to the group
```

A note on the choice: BigQuery is an analytics warehouse, not a low-latency
transactional database, so expect query times in the range of a few hundred
milliseconds to a couple of seconds per lookup (not the sub-100ms you'd get
from a purpose-built vector DB like Pinecone). At your FAQ dataset's size,
this is a fine trade-off if you want everything living in GCP/SQL rather
than another service to manage.

## GCP setup

1. Create (or pick) a GCP project, then enable the BigQuery API:
   ```
   gcloud services enable bigquery.googleapis.com --project=YOUR_PROJECT_ID
   ```

2. Create a dataset to hold the FAQ table:
   ```
   bq mk --dataset --location=US YOUR_PROJECT_ID:faq_bot
   ```
   (Change `faq_bot` if you want a different dataset name — just set
   `BQ_DATASET_ID` to match in step 5.)

3. Create a service account and grant it BigQuery access:
   ```
   gcloud iam service-accounts create faq-bot-sa --project=YOUR_PROJECT_ID

   gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
     --member="serviceAccount:faq-bot-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
     --role="roles/bigquery.dataEditor"

   gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
     --member="serviceAccount:faq-bot-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
     --role="roles/bigquery.jobUser"
   ```

4. Download a key for that service account and point your environment at it:
   ```
   gcloud iam service-accounts keys create faq-bot-key.json \
     --iam-account=faq-bot-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com

   export GOOGLE_APPLICATION_CREDENTIALS="$(pwd)/faq-bot-key.json"
   ```
   Keep this key file out of version control (`.gitignore` it).

5. Set the remaining environment variables:
   ```
   export GCP_PROJECT_ID="YOUR_PROJECT_ID"
   export BQ_DATASET_ID="faq_bot"        # optional, this is the default
   export BQ_TABLE_ID="faq_embeddings"   # optional, this is the default
   export OPENAI_API_KEY="sk-..."
   ```

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

The `terraform/` folder provisions everything except the running bot process
itself:
- The BigQuery dataset + table (matching the schema `build_index.py` expects)
- A dedicated service account for the bot, scoped to just this dataset
  (`bigquery.dataEditor` on the dataset) plus `bigquery.jobUser` at the
  project level (required for running query jobs)
- A dedicated `e2-micro` Compute Engine VM with **no public IP** — reachable
  only via IAP SSH tunneling — plus a separate persistent disk so the
  WhatsApp session (`neonize.db`) survives VM recreation
- A firewall rule allowing SSH only from Google's IAP range

### Apply it

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars with your project_id

terraform init
terraform plan
terraform apply
```

### Get your credentials out of state

```bash
terraform output -raw service_account_key_base64 | base64 -d > ../faq-bot-key.json
```
⚠️ This key resource writes private key material into Terraform state.
Treat `terraform.tfstate` as a secret from this point on — don't commit it,
and use a remote backend (see the commented-out `backend "gcs"` block in
`versions.tf`) rather than leaving it on a laptop. If you'd rather avoid
this entirely, delete the `google_service_account_key` resource from
`main.tf` and create the key manually instead:
```bash
gcloud iam service-accounts keys create faq-bot-key.json \
  --iam-account=$(terraform output -raw service_account_email)
```

### Deploy the bot onto the VM

```bash
# SSH in (no public IP, so this tunnels through IAP)
$(terraform output -raw ssh_command)

# From your machine, in a separate terminal, copy the project files over:
gcloud compute scp --recurse --zone=<your-zone> \
  ../*.py ../*.json ../requirements.txt faq-bot-key.json \
  <vm-name>:/opt/whatsapp-faq-bot/ --tunnel-through-iap
```

Then, on the VM:
```bash
cd /opt/whatsapp-faq-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

export GOOGLE_APPLICATION_CREDENTIALS=/opt/whatsapp-faq-bot/faq-bot-key.json
export GCP_PROJECT_ID=<your-project-id>
export OPENAI_API_KEY=sk-...
export NEONIZE_DB_PATH=/mnt/bot-data/neonize.db   # the persistent disk Terraform attached

python build_index.py         # loads faq_data.json into BigQuery
python whatsapp_bot.py         # scan the QR code - first run only, do this interactively
```

Once you've scanned the QR code once and confirmed it connects, wire it up
as a systemd service so it survives reboots and restarts on crash. A
starter unit file is at `terraform/whatsapp-faq-bot.service` — copy it to
`/etc/systemd/system/`, put the env vars from above into
`/opt/whatsapp-faq-bot/.env`, then:
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
