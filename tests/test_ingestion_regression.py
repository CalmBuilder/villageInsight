import uuid

from village_insight.ingestion_regression import _record_violations


def test_record_violations_accepts_streamed_mapping_rows() -> None:
    index_id = uuid.uuid4()
    source_cell_id = "source:sheet:0:A2"
    record = {
        "sheet_id": "source:sheet:0",
        "raw_data": {
            "contract_version": "dataset-record-raw/v1",
            "source_sha256": "a" * 64,
            "columns": {
                "A": {
                    "source_cell": {
                        "id": source_cell_id,
                        "coordinate": "A2",
                        "raw_value": "示例",
                        "display_value": "示例",
                    }
                }
            },
        },
        "semantic_data": {
            "contract_version": "dataset-record-semantic/v1",
            "fields": {
                "test.field": {
                    "$value": {
                        "value": "示例",
                        "source_cell_id": source_cell_id,
                        "coordinate": "A2",
                    }
                }
            },
        },
    }
    indices = [
        {
            "id": index_id,
            "semantic_field_code": "test.field",
            "role": "",
            "data_type": "text",
            "text_value": "示例",
            "integer_value": None,
            "decimal_value": None,
            "boolean_value": None,
            "date_value": None,
            "datetime_value": None,
        }
    ]
    lineage = {
        index_id: {
            "source_sha256": "a" * 64,
            "sheet_id": "source:sheet:0",
            "source_cell_id": source_cell_id,
            "coordinate": "A2",
            "raw_value": "示例",
            "display_value": "示例",
        }
    }

    violations, raw_count, semantic_count = _record_violations(
        record,
        indices,
        lineage,
    )

    assert violations == []
    assert raw_count == 1
    assert semantic_count == 1
