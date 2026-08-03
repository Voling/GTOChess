resource "aws_secretsmanager_secret" "anthropic" {
  name        = "${local.name}/anthropic-api-key"
  description = "Unset leaves the app on its deterministic provider rather than failing"
}

# Deliberately not seeded here. Put the key in with the console or the CLI so it
# never passes through terraform state.
resource "aws_secretsmanager_secret_version" "anthropic" {
  secret_id     = aws_secretsmanager_secret.anthropic.id
  secret_string = "replace-me"

  lifecycle {
    ignore_changes = [secret_string]
  }
}

resource "aws_secretsmanager_secret" "database" {
  name        = "${local.name}/database-url"
  description = "SQLAlchemy URL for the application database"
}

resource "aws_secretsmanager_secret_version" "database" {
  secret_id = aws_secretsmanager_secret.database.id
  secret_string = format(
    "postgresql+psycopg://%s:%s@%s:%s/%s",
    aws_db_instance.this.username,
    random_password.db.result,
    aws_db_instance.this.address,
    aws_db_instance.this.port,
    aws_db_instance.this.db_name,
  )
}
