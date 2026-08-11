from cryptography.fernet import Fernet
from django.test import SimpleTestCase, override_settings

from bridge_core.credential_crypto import (
    CredentialEncryptionError,
    decrypt_credential,
    encrypt_credential,
)
from bridge_core.models import OperationalConfiguration


TEST_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")


@override_settings(
    ODOO_CREDENTIAL_ENCRYPTION_KEY=TEST_ENCRYPTION_KEY
)
class OdooCredentialEncryptionTests(SimpleTestCase):
    def test_encrypt_and_decrypt_round_trip(self):
        plaintext = "staging-secret-token"

        ciphertext = encrypt_credential(plaintext)

        self.assertNotEqual(ciphertext, plaintext)
        self.assertNotIn(plaintext, ciphertext)
        self.assertEqual(
            decrypt_credential(ciphertext),
            plaintext,
        )

    def test_empty_credential_remains_empty(self):
        self.assertEqual(encrypt_credential(""), "")
        self.assertEqual(decrypt_credential(""), "")

    def test_invalid_ciphertext_fails_closed(self):
        with self.assertRaisesMessage(
            CredentialEncryptionError,
            "could not be decrypted",
        ):
            decrypt_credential("not-valid-fernet-ciphertext")


@override_settings(
    ODOO_CREDENTIAL_ENCRYPTION_KEY=TEST_ENCRYPTION_KEY
)
class OdooConfigurationModelTests(SimpleTestCase):

    def test_poc_runtime_controls_default_fail_closed(self):
        configuration = OperationalConfiguration()

        self.assertEqual(
            configuration.poc_reader_backend,
            OperationalConfiguration.PocReaderBackend.FAKE,
        )
        self.assertFalse(
            configuration.poc_allow_physical_reader_contact
        )
        self.assertFalse(
            configuration.poc_allow_odoo_contact
        )

    def test_default_integration_is_disabled(self):
        configuration = OperationalConfiguration()

        self.assertFalse(
            configuration.odoo_integration_enabled
        )
        self.assertTrue(configuration.odoo_verify_tls)
        self.assertEqual(
            configuration.odoo_request_timeout_seconds,
            10,
        )
        self.assertEqual(
            configuration.odoo_authentication_method,
            OperationalConfiguration
            .OdooAuthenticationMethod.NONE,
        )
        self.assertFalse(configuration.has_odoo_secret)

    def test_model_encrypts_and_decrypts_secret(self):
        configuration = OperationalConfiguration()
        plaintext = "super-secret-staging-token"

        configuration.set_odoo_secret(plaintext)

        self.assertTrue(configuration.has_odoo_secret)
        self.assertNotEqual(
            configuration.odoo_secret_encrypted,
            plaintext,
        )
        self.assertNotIn(
            plaintext,
            configuration.odoo_secret_encrypted,
        )
        self.assertEqual(
            configuration.get_odoo_secret(),
            plaintext,
        )

    def test_setting_empty_secret_clears_ciphertext(self):
        configuration = OperationalConfiguration()
        configuration.set_odoo_secret("temporary-secret")

        configuration.set_odoo_secret("")

        self.assertEqual(
            configuration.odoo_secret_encrypted,
            "",
        )
        self.assertFalse(configuration.has_odoo_secret)
        self.assertEqual(configuration.get_odoo_secret(), "")


class OdooInventoryCountPocConfigurationDefaultsTests(
    SimpleTestCase
):
    def test_poc_defaults_are_fail_closed(self):
        configuration = OperationalConfiguration()

        self.assertFalse(
            configuration.odoo_inventory_count_poc_enabled
        )
        self.assertEqual(
            configuration.odoo_inventory_count_endpoint,
            "/api/rfid/inventory_count",
        )
        self.assertIsNone(
            configuration.odoo_inventory_count_location_id
        )
