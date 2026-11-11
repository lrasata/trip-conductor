variable "region" {
  description = "AWS region to deploy to."
  type        = string
  default     = "eu-central-1" # Frankfurt
}

variable "environment" {
  description = "Environment name, used in resource names (trip-conductor-<environment>-...)."
  type        = string
  default     = "dev"

  validation {
    condition     = can(regex("^[a-z0-9-]{2,10}$", var.environment))
    error_message = "environment must be 2-10 lower-case letters, digits or dashes."
  }
}

variable "alert_email" {
  description = "Email address for alarms and budget warnings."
  type        = string

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.alert_email))
    error_message = "alert_email must be a valid email address."
  }
}

variable "monthly_budget_usd" {
  description = "Monthly spend limit for the whole AWS account. You get an email at 80% (forecast) and 100% (actual)."
  type        = number
  default     = 5
}

variable "services" {
  description = "Services to deploy. Add them one at a time, in order: vendor-simulator, flight, hotel, payment, booking-saga, trip-commands, projector, trip-queries, notification."
  type        = set(string)
  default     = ["vendor-simulator", "flight", "hotel", "payment", "booking-saga", "trip-commands", "projector", "trip-queries", "notification"]

  validation {
    condition     = alltrue([for s in var.services : contains(["vendor-simulator", "flight", "hotel", "payment", "booking-saga", "trip-commands", "projector", "trip-queries", "notification"], s)])
    error_message = "services may only contain: vendor-simulator, flight, hotel, payment, booking-saga, trip-commands, projector, trip-queries, notification."
  }

  validation {
    condition     = !contains(var.services, "flight") || contains(var.services, "vendor-simulator")
    error_message = "flight calls the vendor simulator, so deploy vendor-simulator first."
  }

  validation {
    condition     = !contains(var.services, "hotel") || contains(var.services, "vendor-simulator")
    error_message = "hotel calls the vendor simulator, so deploy vendor-simulator first."
  }

  validation {
    condition     = !contains(var.services, "booking-saga") || (contains(var.services, "flight") && contains(var.services, "hotel"))
    error_message = "booking-saga sends commands to the flight and hotel queues, so deploy both first."
  }
}

variable "vendor_api_rate_limit" {
  description = "Steady-state requests per second allowed on the vendor simulator's public API."
  type        = number
  default     = 20
}

variable "vendor_api_burst_limit" {
  description = "Short burst of requests allowed on the vendor simulator's public API."
  type        = number
  default     = 40
}

variable "lambda_permissions_boundary" {
  description = "ARN of the permissions boundary for service roles. Set in CI (from infra/ci); leave null for local applies."
  type        = string
  default     = null
}
