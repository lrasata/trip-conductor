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

output "vendor_simulator_url" {
  description = "Base URL of the vendor simulator."
  value       = one(aws_apigatewayv2_stage.vendors[*].invoke_url)
}

output "booking_saga_arn" {
  description = "Start a booking from the Step Functions console, with an input from scripts/requests/."
  value       = one(aws_sfn_state_machine.booking_saga[*].arn)
}

output "flight_commands_dlq_url" {
  description = "Where flight commands end up after 3 failed attempts."
  value       = one(module.flight_commands[*].dlq_url)
}

output "hotel_commands_dlq_url" {
  description = "Where hotel commands end up after 3 failed attempts."
  value       = one(module.hotel_commands[*].dlq_url)
}

output "payment_commands_dlq_url" {
  description = "Where payment commands end up after 3 failed attempts."
  value       = one(module.payment_commands[*].dlq_url)
}
