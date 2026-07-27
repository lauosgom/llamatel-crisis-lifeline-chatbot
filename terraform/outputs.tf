output "service_account_email" {
  description = "Email of the bot's service account - set this as the identity behind GOOGLE_APPLICATION_CREDENTIALS."
  value       = google_service_account.faq_bot.email
}

output "service_account_key_base64" {
  description = "Base64-encoded service account key JSON. Decode with: terraform output -raw service_account_key_base64 | base64 -d > faq-bot-key.json"
  value       = google_service_account_key.faq_bot_key.private_key
  sensitive   = true
}

output "bigquery_dataset" {
  description = "Fully-qualified dataset for GCP_PROJECT_ID / BQ_DATASET_ID env vars."
  value       = "${var.project_id}.${google_bigquery_dataset.faq_bot.dataset_id}"
}

output "bigquery_table" {
  description = "Fully-qualified table."
  value       = "${var.project_id}.${google_bigquery_dataset.faq_bot.dataset_id}.${google_bigquery_table.faq_embeddings.table_id}"
}

output "vm_name" {
  description = "Name of the bot VM."
  value       = google_compute_instance.faq_bot.name
}

output "ssh_command" {
  description = "Command to SSH into the VM (no public IP, so this tunnels through IAP)."
  value       = "gcloud compute ssh ${google_compute_instance.faq_bot.name} --zone=${var.zone} --tunnel-through-iap"
}
