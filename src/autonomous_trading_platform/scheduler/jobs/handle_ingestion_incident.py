def handle_ingestion_incident(
    incident_type: str,
    details: dict[str, object],
) -> None:
    print(f"[INGESTION INCIDENT] {incident_type}: {details}")
