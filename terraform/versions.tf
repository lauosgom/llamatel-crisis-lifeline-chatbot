terraform {
  required_version = ">= 1.5"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.0"
    }
  }

  # Local state is fine to start with, but for anything beyond solo use,
  # move state into a GCS bucket so it isn't only on your laptop:
  #
  # backend "gcs" {
  #   bucket = "your-terraform-state-bucket"
  #   prefix = "whatsapp-faq-bot"
  # }
}

provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}
