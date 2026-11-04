# Copy to dev.tfvars (git-ignored) and fill in.
alert_email        = "you@example.com"
monthly_budget_usd = 5

# Deploy one service at a time: add the next one, then plan and apply again.
# Order: vendor-simulator, flight, hotel, payment, booking-saga, trip-commands, projector,
# trip-queries, notification. Remove this line to deploy all of them.
services = []
