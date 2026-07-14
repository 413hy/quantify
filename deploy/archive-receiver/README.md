# Isolated archive receiver

This bundle adds a key-only, chrooted SFTP inbox and a separate processor account. The processor
decrypts each age/X25519 object, verifies both hashes, opens the Parquet footer and schema-version
column, then emits an Ed25519-signed schema `1.1.0` receipt. The sending host receives only the age
recipient and receipt verification public key; the age identity and signing private key stay here.

The receiver is an external disaster-recovery component, not an accepted application host. The
application host platform remains Debian 12 per ADR 0004. Receiver capacity must independently meet
the active stage's retention forecast before a duration gate is started.
