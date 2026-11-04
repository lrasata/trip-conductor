output "event_bus_name" {
  description = "The trip-events bus. Services and the saga publish here."
  value       = aws_cloudwatch_event_bus.trip_events.name
}

output "event_archive_name" {
  description = "Replayed by scripts/rebuild_read_model.py."
  value       = aws_cloudwatch_event_archive.trip_events.name
}

output "ops_alerts_topic_arn" {
  value = aws_sns_topic.ops_alerts.arn
}
