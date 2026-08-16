resource "aws_db_subnet_group" "main" {
  name       = local.name_prefix
  subnet_ids = [for subnet in aws_subnet.database : subnet.id]

  tags = { Name = local.name_prefix }
}

resource "aws_db_instance" "main" {
  identifier = local.name_prefix

  engine         = "postgres"
  engine_version = var.postgres_engine_version
  instance_class = var.database_instance_class

  db_name  = "finsight"
  username = "finsight_admin"
  port     = 5432

  manage_master_user_password = true

  allocated_storage     = 20
  max_allocated_storage = 100
  storage_type          = "gp3"
  storage_encrypted     = true

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.database.id]
  publicly_accessible    = false
  multi_az               = var.database_multi_az

  backup_retention_period    = var.database_backup_retention_days
  backup_window              = "03:00-04:00"
  maintenance_window         = "sun:04:00-sun:05:00"
  auto_minor_version_upgrade = true
  copy_tags_to_snapshot      = true

  deletion_protection = var.database_deletion_protection
  skip_final_snapshot = true

  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]
  performance_insights_enabled    = false

  apply_immediately = false
}
