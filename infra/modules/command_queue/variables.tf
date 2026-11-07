variable "name_prefix" {
  description = "Prefix for resource names, e.g. trip-conductor-dev."
  type        = string
}

variable "service_name" {
  description = "Receiving service, e.g. flight. Becomes part of the queue name."
  type        = string
}

variable "alarm_topic_arn" {
  description = "SNS topic that receives the queue's alarms."
  type        = string
}

variable "visibility_timeout_seconds" {
  description = "How long a message stays hidden while a consumer works on it. AWS recommends 6x the Lambda timeout."
  type        = number
  default     = 180
}

variable "max_receive_count" {
  description = "Failed attempts before a message is moved to the dead-letter queue."
  type        = number
  default     = 3

  validation {
    condition     = var.max_receive_count >= 1 && var.max_receive_count <= 10
    error_message = "max_receive_count must be between 1 and 10."
  }
}

variable "max_message_age_seconds" {
  description = "Alarm when the oldest message in the queue is older than this."
  type        = number
  default     = 120 # a saga task only waits 60 s for its answer
}
