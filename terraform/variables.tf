variable "credentials" {
  description = "Path to GCP credentials JSON file"
  default     = "./keys/credentials.json"
}

variable "project_id" {
  description = "GCP project ID that will host the BigQuery dataset/table and the bot VM."
  default        = "singular-arbor-401018"
}

variable "region" {
  description = "GCP region for regional resources (the VM's disk, etc)."
  type        = string
  default     = "us-east1"
}

variable "zone" {
  description = "GCP zone for the bot VM."
  type        = string
  default     = "us-central1-a"
}

variable "bq_location" {
  description = "BigQuery dataset location. US/EU multi-region or a specific region."
  type        = string
  default     = "US"
}

variable "dataset_id" {
  description = "BigQuery dataset ID for the FAQ table."
  type        = string
  default     = "faq_bot"
}

variable "table_id" {
  description = "BigQuery table ID for the FAQ embeddings."
  type        = string
  default     = "faq_embeddings"
}

variable "vm_name" {
  description = "Name of the Compute Engine VM that will run the WhatsApp bot."
  type        = string
  default     = "whatsapp-faq-bot"
}

variable "machine_type" {
  description = "Machine type for the bot VM. e2-micro is free-tier eligible in some US regions."
  type        = string
  default     = "e2-micro"
}

variable "boot_disk_size_gb" {
  description = "Size of the VM's boot disk in GB."
  type        = number
  default     = 20
}

variable "data_disk_size_gb" {
  description = "Size of the separate persistent disk that stores the WhatsApp session (neonize.db), so it survives VM recreation."
  type        = number
  default     = 10
}

variable "ssh_source_ranges" {
  description = "IP ranges allowed to SSH into the VM via IAP. Defaults to Google's IAP forwarding range only (no public SSH exposure)."
  type        = list(string)
  default     = ["35.235.240.0/20"]
}
