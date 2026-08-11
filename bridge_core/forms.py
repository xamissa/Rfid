from django import forms

from .models import OperationalConfiguration, ReaderDevice


class ReaderDeviceForm(forms.ModelForm):
    class Meta:
        model = ReaderDevice
        fields = (
            "code",
            "name",
            "role",
            "host",
            "port",
            "device_address",
            "inventory_mode",
            "connect_timeout_seconds",
            "read_timeout_seconds",
            "reconnect_delay_seconds",
            "enabled",
            "notes",
        )
        widgets = {
            "code": forms.TextInput(
                attrs={
                    "autocomplete": "off",
                    "placeholder": "receiving-door-01",
                }
            ),
            "name": forms.TextInput(
                attrs={
                    "autocomplete": "off",
                    "placeholder": "Receiving door reader",
                }
            ),
            "notes": forms.Textarea(
                attrs={
                    "rows": 4,
                    "placeholder": (
                        "Physical location, antenna notes, or installation "
                        "details."
                    ),
                }
            ),
        }

    def clean_code(self):
        return self.cleaned_data["code"].strip().lower()

    def clean_name(self):
        name = self.cleaned_data["name"].strip()

        if not name:
            raise forms.ValidationError("Reader name is required.")

        return name

    def clean_host(self):
        return self.cleaned_data["host"].strip()

    def clean(self):
        cleaned_data = super().clean()

        host = cleaned_data.get("host", "")
        port = cleaned_data.get("port")
        device_address = cleaned_data.get("device_address")
        connect_timeout = cleaned_data.get("connect_timeout_seconds")
        read_timeout = cleaned_data.get("read_timeout_seconds")
        reconnect_delay = cleaned_data.get("reconnect_delay_seconds")
        enabled = cleaned_data.get("enabled")

        if enabled and not host:
            self.add_error(
                "host",
                "A reader hostname or IP address is required when enabled.",
            )

        if port is not None and not 1 <= port <= 65535:
            self.add_error(
                "port",
                "TCP port must be between 1 and 65535.",
            )

        if (
            device_address is not None
            and not 0 <= device_address <= 255
        ):
            self.add_error(
                "device_address",
                "Device address must be between 0 and 255.",
            )

        timeout_fields = (
            ("connect_timeout_seconds", connect_timeout),
            ("read_timeout_seconds", read_timeout),
            ("reconnect_delay_seconds", reconnect_delay),
        )

        for field_name, value in timeout_fields:
            if value is not None and not 1 <= value <= 300:
                self.add_error(
                    field_name,
                    "Value must be between 1 and 300 seconds.",
                )

        return cleaned_data


POC_RUNTIME_ENABLE_CONFIRMATION_PHRASE = (
    "ENABLE_CONTROLLED_POC_CONTACT"
)


class PocRuntimeControlForm(forms.ModelForm):
    confirmation = forms.CharField(
        required=False,
        label="Confirmation phrase",
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off",
                "placeholder": (
                    POC_RUNTIME_ENABLE_CONFIRMATION_PHRASE
                ),
            }
        ),
        help_text=(
            "Required when enabling physical-reader or Odoo contact."
        ),
    )

    class Meta:
        model = OperationalConfiguration
        fields = (
            "poc_reader_backend",
            "poc_allow_physical_reader_contact",
            "poc_allow_odoo_contact",
        )
        labels = {
            "poc_reader_backend": "Reader test backend",
            "poc_allow_physical_reader_contact": (
                "Allow controlled RFID reader contact"
            ),
            "poc_allow_odoo_contact": (
                "Allow controlled Odoo.sh staging contact"
            ),
        }
        help_texts = {
            "poc_reader_backend": (
                "Use Fake reader while configuring. Cached TCP reads "
                "reader memory; Active TCP starts and stops a controlled "
                "live inventory scan."
            ),
            "poc_allow_physical_reader_contact": (
                "Allows staff-only one-shot reader tests. It does not "
                "start the worker."
            ),
            "poc_allow_odoo_contact": (
                "Allows staff-only Odoo connection and inventory-count "
                "POC requests to the configured staging database."
            ),
        }

    def clean(self):
        cleaned_data = super().clean()

        backend = cleaned_data.get("poc_reader_backend")
        allow_reader = cleaned_data.get(
            "poc_allow_physical_reader_contact"
        )
        allow_odoo = cleaned_data.get(
            "poc_allow_odoo_contact"
        )
        confirmation = (
            cleaned_data.get("confirmation") or ""
        ).strip()

        enabling_contact = bool(
            allow_reader or allow_odoo
        )

        if (
            enabling_contact
            and confirmation
            != POC_RUNTIME_ENABLE_CONFIRMATION_PHRASE
        ):
            self.add_error(
                "confirmation",
                "Enter the exact confirmation phrase before enabling "
                "external contact.",
            )

        physical_backends = {
            OperationalConfiguration.PocReaderBackend.CACHED_TCP,
            OperationalConfiguration.PocReaderBackend.ACTIVE_TCP,
        }

        if allow_reader and backend not in physical_backends:
            self.add_error(
                "poc_reader_backend",
                "Physical reader contact requires the Cached TCP "
                "or Active TCP reader backend.",
            )

        if (
            backend
            == OperationalConfiguration
            .PocReaderBackend
            .FAKE
            and allow_reader
        ):
            self.add_error(
                "poc_allow_physical_reader_contact",
                "Physical contact cannot be enabled with the fake "
                "reader backend.",
            )

        return cleaned_data


class OperationalConfigurationForm(forms.ModelForm):
    class Meta:
        model = OperationalConfiguration
        fields = (
            "worker_batch_size",
            "max_delivery_attempts",
            "retry_initial_seconds",
            "retry_max_seconds",
            "event_retention_days",
            "notes",
        )
        widgets = {
            "notes": forms.Textarea(
                attrs={
                    "rows": 4,
                    "placeholder": (
                        "Document why these operational settings were changed."
                    ),
                }
            ),
        }

    def clean(self):
        cleaned_data = super().clean()

        batch_size = cleaned_data.get("worker_batch_size")
        max_attempts = cleaned_data.get("max_delivery_attempts")
        retry_initial = cleaned_data.get("retry_initial_seconds")
        retry_max = cleaned_data.get("retry_max_seconds")
        retention_days = cleaned_data.get("event_retention_days")

        if batch_size is not None and not 1 <= batch_size <= 1000:
            self.add_error(
                "worker_batch_size",
                "Batch size must be between 1 and 1000.",
            )

        if max_attempts is not None and not 1 <= max_attempts <= 100:
            self.add_error(
                "max_delivery_attempts",
                "Maximum delivery attempts must be between 1 and 100.",
            )

        if retry_initial is not None and retry_initial < 1:
            self.add_error(
                "retry_initial_seconds",
                "Initial retry delay must be at least one second.",
            )

        if retry_max is not None and retry_max < 1:
            self.add_error(
                "retry_max_seconds",
                "Maximum retry delay must be at least one second.",
            )

        if (
            retry_initial is not None
            and retry_max is not None
            and retry_max < retry_initial
        ):
            self.add_error(
                "retry_max_seconds",
                "Maximum retry delay cannot be less than the initial delay.",
            )

        if retention_days is not None and not 1 <= retention_days <= 3650:
            self.add_error(
                "event_retention_days",
                "Retention must be between 1 and 3650 days.",
            )

        return cleaned_data

PHYSICAL_CONTACT_CONFIRMATION_PHRASE = (
    "CONTACT_THIS_READER_ONCE"
)


class PhysicalReaderTestForm(forms.Form):
    scan_seconds = forms.FloatField(
        min_value=0,
        max_value=300,
        initial=3.0,
        help_text=(
            "How long the cached inventory scan should run."
        ),
    )
    confirmation = forms.CharField(
        max_length=64,
        help_text=(
            "Enter CONTACT_THIS_READER_ONCE exactly."
        ),
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off",
                "placeholder": (
                    "CONTACT_THIS_READER_ONCE"
                ),
            }
        ),
    )

    def clean_confirmation(self):
        confirmation = self.cleaned_data[
            "confirmation"
        ]

        if (
            confirmation
            != PHYSICAL_CONTACT_CONFIRMATION_PHRASE
        ):
            raise forms.ValidationError(
                "Enter the exact physical-contact "
                "confirmation phrase."
            )

        return confirmation


class OdooInventoryCountPocManualForm(forms.Form):
    rfid_tags = forms.CharField(
        label="RFID tags",
        help_text=(
            "Enter one EPC per line, or separate tags with commas. "
            "Duplicates and blank values are removed."
        ),
        widget=forms.Textarea(
            attrs={
                "rows": 8,
                "autocomplete": "off",
                "placeholder": (
                    "E2000017221101441890ABCD\n"
                    "E2000017221101441890ABCE"
                ),
            },
        ),
    )

    def clean_rfid_tags(self):
        raw_value = self.cleaned_data["rfid_tags"]
        normalized_separators = raw_value.replace(",", "\n")

        tags = tuple(
            value.strip()
            for value in normalized_separators.splitlines()
            if value.strip()
        )

        if not tags:
            raise forms.ValidationError(
                "At least one RFID tag is required."
            )

        if len(tags) > 500:
            raise forms.ValidationError(
                "A maximum of 500 RFID tags may be submitted at once."
            )

        return tags


class OdooIntegrationConfigurationForm(forms.ModelForm):
    odoo_secret = forms.CharField(
        required=False,
        max_length=4096,
        label="Credential secret",
        help_text=(
            "Leave blank to preserve the currently stored secret. "
            "The value is encrypted before it is stored."
        ),
        widget=forms.PasswordInput(
            render_value=False,
            attrs={
                "autocomplete": "new-password",
                "placeholder": (
                    "Leave blank to preserve existing secret"
                ),
            },
        ),
    )
    clear_odoo_secret = forms.BooleanField(
        required=False,
        label="Clear stored credential",
        help_text=(
            "Remove the currently stored encrypted Odoo credential."
        ),
    )

    class Meta:
        model = OperationalConfiguration
        fields = (
            "odoo_base_url",
            "odoo_database",
            "odoo_session_endpoint",
            "odoo_event_endpoint",
            "odoo_authentication_method",
            "odoo_client_identifier",
            "odoo_request_timeout_seconds",
            "odoo_verify_tls",
            "odoo_integration_enabled",
            "odoo_inventory_count_poc_enabled",
            "odoo_inventory_count_endpoint",
            "odoo_inventory_count_location_id",
        )
        widgets = {
            "odoo_base_url": forms.URLInput(
                attrs={
                    "autocomplete": "off",
                    "placeholder": (
                        "https://example-staging.odoo.com"
                    ),
                },
            ),
            "odoo_database": forms.TextInput(
                attrs={
                    "autocomplete": "off",
                    "placeholder": (
                        "Optional database or environment identifier"
                    ),
                },
            ),
            "odoo_session_endpoint": forms.TextInput(
                attrs={
                    "autocomplete": "off",
                    "placeholder": "/rfid/session",
                },
            ),
            "odoo_event_endpoint": forms.TextInput(
                attrs={
                    "autocomplete": "off",
                    "placeholder": "/rfid/events",
                },
            ),
            "odoo_client_identifier": forms.TextInput(
                attrs={
                    "autocomplete": "off",
                    "placeholder": (
                        "Odoo login email or integration identifier"
                    ),
                },
            ),
            "odoo_inventory_count_endpoint": forms.TextInput(
                attrs={
                    "autocomplete": "off",
                    "placeholder": (
                        "/api/rfid/inventory_count"
                    ),
                },
            ),
            "odoo_inventory_count_location_id": (
                forms.NumberInput(
                    attrs={
                        "min": 1,
                        "step": 1,
                        "placeholder": (
                            "Odoo staging stock.location ID"
                        ),
                    },
                )
            ),
        }

    def clean_odoo_base_url(self):
        return self.cleaned_data["odoo_base_url"].strip()

    def clean_odoo_database(self):
        return self.cleaned_data["odoo_database"].strip()

    def clean_odoo_session_endpoint(self):
        return self.cleaned_data[
            "odoo_session_endpoint"
        ].strip()

    def clean_odoo_event_endpoint(self):
        return self.cleaned_data[
            "odoo_event_endpoint"
        ].strip()

    def clean_odoo_client_identifier(self):
        return self.cleaned_data[
            "odoo_client_identifier"
        ].strip()

    def clean_odoo_inventory_count_endpoint(self):
        return self.cleaned_data[
            "odoo_inventory_count_endpoint"
        ].strip()

    def clean(self):
        cleaned_data = super().clean()

        timeout = cleaned_data.get(
            "odoo_request_timeout_seconds"
        )
        authentication_method = cleaned_data.get(
            "odoo_authentication_method"
        )
        client_identifier = cleaned_data.get(
            "odoo_client_identifier",
            "",
        )
        supplied_secret = cleaned_data.get(
            "odoo_secret",
            "",
        )
        clear_secret = cleaned_data.get(
            "clear_odoo_secret",
            False,
        )
        integration_enabled = cleaned_data.get(
            "odoo_integration_enabled",
            False,
        )
        base_url = cleaned_data.get(
            "odoo_base_url",
            "",
        )
        session_endpoint = cleaned_data.get(
            "odoo_session_endpoint",
            "",
        )
        event_endpoint = cleaned_data.get(
            "odoo_event_endpoint",
            "",
        )
        poc_enabled = cleaned_data.get(
            "odoo_inventory_count_poc_enabled",
            False,
        )
        poc_endpoint = cleaned_data.get(
            "odoo_inventory_count_endpoint",
            "",
        )
        poc_location_id = cleaned_data.get(
            "odoo_inventory_count_location_id"
        )
        database = cleaned_data.get(
            "odoo_database",
            "",
        )

        if timeout is not None and not 1 <= timeout <= 300:
            self.add_error(
                "odoo_request_timeout_seconds",
                "Timeout must be between 1 and 300 seconds.",
            )

        if supplied_secret and clear_secret:
            self.add_error(
                "clear_odoo_secret",
                "Cannot supply and clear the credential together.",
            )

        has_existing_secret = bool(
            self.instance
            and self.instance.pk
            and self.instance.has_odoo_secret
        )
        effective_secret_exists = (
            bool(supplied_secret)
            or (
                has_existing_secret
                and not clear_secret
            )
        )

        authenticated_methods = {
            OperationalConfiguration
            .OdooAuthenticationMethod.BEARER_TOKEN,
            OperationalConfiguration
            .OdooAuthenticationMethod.BASIC,
            OperationalConfiguration
            .OdooAuthenticationMethod.API_KEY,
            OperationalConfiguration
            .OdooAuthenticationMethod.ODOO_SESSION,
        }

        if (
            authentication_method
            in authenticated_methods
            and not effective_secret_exists
        ):
            self.add_error(
                "odoo_secret",
                "The selected authentication method requires a credential.",
            )

        identifier_required_methods = {
            OperationalConfiguration
            .OdooAuthenticationMethod.BASIC,
            OperationalConfiguration
            .OdooAuthenticationMethod.ODOO_SESSION,
        }

        if (
            authentication_method
            in identifier_required_methods
            and not client_identifier
        ):
            self.add_error(
                "odoo_client_identifier",
                (
                    "A username or client identifier is required "
                    "for the selected authentication method."
                ),
            )

        if poc_enabled:
            if not base_url:
                self.add_error(
                    "odoo_base_url",
                    (
                        "Odoo base URL is required when the "
                        "inventory-count POC is enabled."
                    ),
                )

            if not database:
                self.add_error(
                    "odoo_database",
                    (
                        "Odoo database is required when the "
                        "inventory-count POC is enabled."
                    ),
                )

            if not poc_endpoint:
                self.add_error(
                    "odoo_inventory_count_endpoint",
                    (
                        "Inventory-count endpoint is required "
                        "when the POC is enabled."
                    ),
                )

            if poc_location_id is None:
                self.add_error(
                    "odoo_inventory_count_location_id",
                    (
                        "A staging location ID is required when "
                        "the inventory-count POC is enabled."
                    ),
                )

            if (
                authentication_method
                != OperationalConfiguration
                .OdooAuthenticationMethod.ODOO_SESSION
            ):
                self.add_error(
                    "odoo_authentication_method",
                    (
                        "The inventory validation configuration requires "
                        "Odoo username and password session "
                        "authentication."
                    ),
                )

            if not client_identifier:
                self.add_error(
                    "odoo_client_identifier",
                    (
                        "Odoo login username is required when "
                        "the inventory-count POC is enabled."
                    ),
                )

            if not effective_secret_exists:
                self.add_error(
                    "odoo_secret",
                    (
                        "Odoo login password is required when "
                        "the inventory-count POC is enabled."
                    ),
                )

        if integration_enabled:
            required_values = (
                ("odoo_base_url", base_url),
                ("odoo_session_endpoint", session_endpoint),
                ("odoo_event_endpoint", event_endpoint),
            )

            for field_name, value in required_values:
                if not value:
                    self.add_error(
                        field_name,
                        "This field is required when integration is enabled.",
                    )

            if (
                authentication_method
                in authenticated_methods
                and not effective_secret_exists
            ):
                self.add_error(
                    "odoo_secret",
                    "A credential is required before integration can be enabled.",
                )

        return cleaned_data

    def save(self, commit=True):
        configuration = super().save(commit=False)

        supplied_secret = self.cleaned_data.get(
            "odoo_secret",
            "",
        )
        clear_secret = self.cleaned_data.get(
            "clear_odoo_secret",
            False,
        )

        if clear_secret:
            configuration.set_odoo_secret("")
        elif supplied_secret:
            configuration.set_odoo_secret(
                supplied_secret
            )

        if commit:
            configuration.save()

        return configuration


class InitialPasswordChangeForm(forms.Form):
    old_password = forms.CharField(
        label="Current password",
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "current-password",
            }
        ),
    )

    new_password1 = forms.CharField(
        label="New password",
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "new-password",
            }
        ),
    )

    new_password2 = forms.CharField(
        label="Confirm new password",
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "new-password",
            }
        ),
    )

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_old_password(self):
        password = self.cleaned_data["old_password"]

        if not self.user.check_password(password):
            raise forms.ValidationError(
                "Your current password is incorrect."
            )

        return password

    def clean(self):
        cleaned_data = super().clean()

        password1 = cleaned_data.get("new_password1")
        password2 = cleaned_data.get("new_password2")

        if password1 and password2 and password1 != password2:
            raise forms.ValidationError(
                "The new passwords do not match."
            )

        if password1:
            from django.contrib.auth.password_validation import (
                validate_password,
            )

            validate_password(
                password1,
                self.user,
            )

        return cleaned_data


ACTIVE_SESSION_SCAN_CONFIRMATION_PHRASE = (
    "SCAN_AND_STORE_ACTIVE_SESSION"
)


class ActiveSessionScanForm(forms.Form):
    scan_seconds = forms.FloatField(
        min_value=1,
        max_value=60,
        initial=10,
        label="Scan duration",
        help_text=(
            "Run one controlled active inventory scan for "
            "between 1 and 60 seconds."
        ),
    )
    confirmation = forms.CharField(
        max_length=64,
        label="Confirmation phrase",
        help_text=(
            "Enter SCAN_AND_STORE_ACTIVE_SESSION exactly."
        ),
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off",
                "placeholder": (
                    ACTIVE_SESSION_SCAN_CONFIRMATION_PHRASE
                ),
            }
        ),
    )

    def clean_confirmation(self):
        confirmation = self.cleaned_data["confirmation"]

        if (
            confirmation
            != ACTIVE_SESSION_SCAN_CONFIRMATION_PHRASE
        ):
            raise forms.ValidationError(
                "Enter the exact active-session scan "
                "confirmation phrase."
            )

        return confirmation
