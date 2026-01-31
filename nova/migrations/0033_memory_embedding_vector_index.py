from django.db import migrations


def create_hnsw_index(apps, schema_editor):
    """Create pgvector HNSW index (PostgreSQL only).

    Tests run on SQLite; this migration must be a no-op there.
    """

    if schema_editor.connection.vendor != "postgresql":
        return

    schema_editor.execute(
        "CREATE INDEX IF NOT EXISTS idx_memory_item_embedding_vector_hnsw "
        "ON nova_memoryitemembedding USING hnsw (vector vector_cosine_ops);"
    )


def drop_hnsw_index(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    schema_editor.execute(
        "DROP INDEX IF EXISTS idx_memory_item_embedding_vector_hnsw;"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("nova", "0032_userparameters_memory_embeddings"),
    ]

    operations = [
        # pgvector index (PostgreSQL only)
        migrations.RunPython(create_hnsw_index, reverse_code=drop_hnsw_index),
    ]
