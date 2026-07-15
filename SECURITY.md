# Security

This repository is an MVP that is intended for synthetic patient data only.
The shared-password authentication flow is not suitable for production or real
patient information.

Do not commit `.env`, Google service-account keys, OAuth authorization codes,
Neo4j credentials, patient exports, or other secrets. Use Google Secret Manager
for deployed credentials.

Report security concerns privately to the repository maintainers rather than
opening a public issue containing credentials or patient information.
