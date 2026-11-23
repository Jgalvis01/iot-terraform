output "bucket_name" {
  value = local.bucket_id
}

output "bucket_arn" {
  value = local.bucket_arn
}

output "prefixes" {
  description = "Prefijos lógicos usados por la plataforma."
  value = {
    reports_async = "reports/{yyyy}/{mm}/{job_id}.json"
    reports_daily = "reports/daily/{date}/movements-{date}.json"
    warehouses    = "warehouses/{WH}/{yyyy}/{mm}/inventory-{date}.json"
    exports       = "exports/  (dispara Lambda, expira ${var.expire_days} d)"
    processed     = "processed/ (salida Lambda, expira ${var.expire_days} d)"
    rejected      = "rejected/"
  }
}
