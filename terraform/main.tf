##############################
# BigQuery: dataset + table
##############################

resource "google_bigquery_dataset" "faq_bot" {
  dataset_id  = var.dataset_id
  location    = var.bq_location
  description = "FAQ embeddings for the WhatsApp RAG bot"
}

resource "google_bigquery_table" "faq_embeddings" {
  dataset_id = google_bigquery_dataset.faq_bot.dataset_id
  table_id   = var.table_id

  # Safety net: `terraform destroy` won't silently drop your embeddings.
  # Remove this (or set to false) once you're comfortable managing the
  # table lifecycle entirely through Terraform.
  deletion_protection = true

  schema = jsonencode([
    { name = "id", type = "STRING", mode = "REQUIRED" },
    { name = "question", type = "STRING", mode = "REQUIRED" },
    { name = "answer", type = "STRING", mode = "REQUIRED" },
    { name = "embedding", type = "FLOAT64", mode = "REPEATED" },
  ])
}

resource "google_bigquery_table" "query_logs" {
  dataset_id = google_bigquery_dataset.faq_bot.dataset_id
  table_id   = var.query_log_table_id

  deletion_protection = true

  time_partitioning {
    type  = "DAY"
    field = "timestamp"
  }

  schema = jsonencode([
    { name = "id", type = "STRING", mode = "REQUIRED" },
    { name = "timestamp", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "question", type = "STRING", mode = "REQUIRED" },
    { name = "matched_faq_id", type = "STRING", mode = "NULLABLE" },
    { name = "distance", type = "FLOAT64", mode = "NULLABLE" },
    { name = "responded", type = "BOOL", mode = "REQUIRED" },
    { name = "answer", type = "STRING", mode = "NULLABLE" },
  ])
}

##############################
# Monitoring dashboard views (for Looker Studio) - pre-aggregated so the
# dashboard queries a small view instead of scanning/aggregating raw
# query_logs rows on every page load. Views are plain SQL, no dbt needed
# at this scale - see the depends_on on each, which ensures Terraform
# creates the underlying tables before any view that reads them.
##############################

resource "google_bigquery_table" "query_logs_daily_stats" {
  dataset_id = google_bigquery_dataset.faq_bot.dataset_id
  table_id   = "query_logs_daily_stats"

  view {
    use_legacy_sql = false
    query          = <<-SQL
      SELECT
        DATE(timestamp) AS day,
        COUNT(*) AS total_questions,
        COUNTIF(responded) AS answered,
        COUNTIF(NOT responded) AS unanswered,
        SAFE_DIVIDE(COUNTIF(responded), COUNT(*)) AS response_rate
      FROM `${var.project_id}.${google_bigquery_dataset.faq_bot.dataset_id}.${var.query_log_table_id}`
      GROUP BY day
      ORDER BY day
    SQL
  }

  depends_on = [google_bigquery_table.query_logs]
}

resource "google_bigquery_table" "query_logs_top_faqs" {
  dataset_id = google_bigquery_dataset.faq_bot.dataset_id
  table_id   = "query_logs_top_faqs"

  view {
    use_legacy_sql = false
    query          = <<-SQL
      SELECT
        q.matched_faq_id,
        f.question AS faq_question,
        COUNT(*) AS times_asked,
        AVG(q.distance) AS avg_distance
      FROM `${var.project_id}.${google_bigquery_dataset.faq_bot.dataset_id}.${var.query_log_table_id}` q
      LEFT JOIN `${var.project_id}.${google_bigquery_dataset.faq_bot.dataset_id}.${var.table_id}` f
        ON q.matched_faq_id = f.id
      WHERE q.responded = TRUE
      GROUP BY q.matched_faq_id, f.question
      ORDER BY times_asked DESC
    SQL
  }

  depends_on = [
    google_bigquery_table.query_logs,
    google_bigquery_table.faq_embeddings,
  ]
}

resource "google_bigquery_table" "query_logs_distance_distribution" {
  dataset_id = google_bigquery_dataset.faq_bot.dataset_id
  table_id   = "query_logs_distance_distribution"

  view {
    use_legacy_sql = false
    query          = <<-SQL
      SELECT
        CASE
          WHEN distance IS NULL THEN 'no_match'
          WHEN distance <= 0.10 THEN '0.00-0.10'
          WHEN distance <= 0.20 THEN '0.10-0.20'
          WHEN distance <= 0.25 THEN '0.20-0.25'
          WHEN distance <= 0.35 THEN '0.25-0.35'
          WHEN distance <= 0.50 THEN '0.35-0.50'
          ELSE '0.50+'
        END AS distance_bucket,
        responded,
        COUNT(*) AS count
      FROM `${var.project_id}.${google_bigquery_dataset.faq_bot.dataset_id}.${var.query_log_table_id}`
      GROUP BY distance_bucket, responded
      ORDER BY distance_bucket
    SQL
  }

  depends_on = [google_bigquery_table.query_logs]
}

##############################
# Service account for the bot
##############################

resource "google_service_account" "faq_bot" {
  account_id   = "faq-bot-sa"
  display_name = "WhatsApp FAQ bot service account"
}

# Dataset-scoped access (least privilege) rather than project-wide
# bigquery.dataEditor, since the bot only ever touches this one dataset.
resource "google_bigquery_dataset_iam_member" "faq_bot_data_editor" {
  dataset_id = google_bigquery_dataset.faq_bot.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.faq_bot.email}"
}

# bigquery.jobUser has no dataset-level equivalent — running a query job
# is inherently a project-level permission.
resource "google_project_iam_member" "faq_bot_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.faq_bot.email}"
}

# NOTE ON KEYS: this resource writes the private key material into
# Terraform state. That's convenient for a first pass, but treat your
# state file as a secret from this point on (definitely don't commit it,
# and move to a remote backend with encryption/access control - see
# versions.tf). A more security-conscious alternative is to omit this
# resource entirely and instead run:
#   gcloud iam service-accounts keys create faq-bot-key.json \
#     --iam-account=${google_service_account.faq_bot.email}
# by hand, outside of Terraform, so the key never touches state.
resource "google_service_account_key" "faq_bot_key" {
  service_account_id = google_service_account.faq_bot.name
}

##############################
# Networking: SSH via IAP only, no public SSH exposure
##############################

resource "google_compute_firewall" "allow_iap_ssh" {
  name    = "allow-iap-ssh-${var.vm_name}"
  network = "default"

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  source_ranges = var.ssh_source_ranges
  target_tags   = [var.vm_name]
}

##############################
# Persistent disk for the WhatsApp session (survives VM recreation)
##############################

resource "google_compute_disk" "bot_data" {
  name = "${var.vm_name}-data"
  zone = var.zone
  size = var.data_disk_size_gb
  type = "pd-standard"

}

##############################
# The bot VM
##############################

resource "google_compute_instance" "faq_bot" {
  name         = var.vm_name
  machine_type = var.machine_type
  zone         = var.zone
  tags         = [var.vm_name]

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = var.boot_disk_size_gb
    }
  }

  attached_disk {
    source      = google_compute_disk.bot_data.self_link
    device_name = "bot-data"
  }

  network_interface {
    network = "default"
    # No access_config block => no public IP. Reach the VM via
    # `gcloud compute ssh --tunnel-through-iap` (see outputs.tf).
  }

  service_account {
    email  = google_service_account.faq_bot.email
    scopes = ["cloud-platform"]
  }

  # Base packages only - deploy and run the bot itself manually the first
  # time (it needs an interactive QR scan), then wire up systemd.
  metadata_startup_script = <<-EOT
    #!/bin/bash
    set -e
    apt-get update
    apt-get install -y python3 python3-pip python3-venv git

    # Format + mount the persistent data disk on first boot only.
    DATA_DISK=/dev/disk/by-id/google-bot-data
    MOUNT_POINT=/mnt/bot-data
    mkdir -p "$MOUNT_POINT"
    if ! blkid "$DATA_DISK" >/dev/null 2>&1; then
      mkfs.ext4 -m 0 -F -E lazy_itable_init=0,lazy_journal_init=0,discard "$DATA_DISK"
    fi
    mount -o discard,defaults "$DATA_DISK" "$MOUNT_POINT" || true
    grep -q "$MOUNT_POINT" /etc/fstab || echo "$DATA_DISK $MOUNT_POINT ext4 discard,defaults,nofail 0 2" >> /etc/fstab
  EOT

}
