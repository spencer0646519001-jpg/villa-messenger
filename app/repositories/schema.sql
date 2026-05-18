CREATE TABLE IF NOT EXISTS tenants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    timezone TEXT NOT NULL,
    default_language TEXT NOT NULL,
    emergency_phone TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tenant_channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    platform TEXT NOT NULL,
    channel_id TEXT,
    channel_name TEXT,
    access_token_ref TEXT,
    channel_secret_ref TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(tenant_id, platform, channel_id),
    FOREIGN KEY (tenant_id) REFERENCES tenants(id)
);

CREATE TABLE IF NOT EXISTS tenant_owners (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    platform TEXT NOT NULL,
    platform_user_id TEXT NOT NULL,
    display_name TEXT,
    role TEXT NOT NULL DEFAULT 'owner',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(tenant_id, platform, platform_user_id),
    FOREIGN KEY (tenant_id) REFERENCES tenants(id)
);

CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    platform TEXT NOT NULL,
    platform_user_id TEXT NOT NULL,
    display_name TEXT,
    role TEXT NOT NULL DEFAULT 'guest',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(tenant_id, platform, platform_user_id),
    FOREIGN KEY (tenant_id) REFERENCES tenants(id)
);

CREATE TABLE IF NOT EXISTS reservations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    booking_code TEXT NOT NULL,
    guest_name TEXT,
    checkin_date TEXT,
    checkout_date TEXT,
    room_name TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(tenant_id, booking_code),
    FOREIGN KEY (tenant_id) REFERENCES tenants(id)
);

CREATE TABLE IF NOT EXISTS conversation_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    contact_id INTEGER NOT NULL,
    reservation_id INTEGER NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(tenant_id, contact_id, reservation_id),
    FOREIGN KEY (tenant_id) REFERENCES tenants(id),
    FOREIGN KEY (contact_id) REFERENCES contacts(id),
    FOREIGN KEY (reservation_id) REFERENCES reservations(id)
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    platform TEXT NOT NULL,
    platform_user_id TEXT NOT NULL,
    contact_id INTEGER,
    reservation_id INTEGER,
    message_text TEXT NOT NULL,
    category TEXT NOT NULL,
    risk_level INTEGER,
    reply_text TEXT,
    is_night INTEGER NOT NULL,
    is_urgent INTEGER NOT NULL DEFAULT 0,
    needs_manual_followup INTEGER NOT NULL DEFAULT 0,
    send_alert_to_owner INTEGER NOT NULL DEFAULT 0,
    handled INTEGER NOT NULL DEFAULT 0,
    system_state_at_time TEXT NOT NULL DEFAULT 'unknown',
    created_at TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id),
    FOREIGN KEY (contact_id) REFERENCES contacts(id),
    FOREIGN KEY (reservation_id) REFERENCES reservations(id)
);

CREATE TABLE IF NOT EXISTS inquiries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    contact_id INTEGER,
    message_id INTEGER,
    platform TEXT NOT NULL,
    platform_user_id TEXT NOT NULL,
    checkin_date TEXT,
    checkout_date TEXT,
    nights INTEGER,
    adult_count INTEGER,
    child_count INTEGER,
    infant_count INTEGER,
    guest_count INTEGER,
    has_pet INTEGER NOT NULL DEFAULT 0,
    pet_count INTEGER,
    pet_type TEXT,
    pet_fee_per_pet INTEGER,
    pet_fee_total INTEGER,
    needs_pet_count_confirmation INTEGER NOT NULL DEFAULT 0,
    inquiry_type TEXT NOT NULL,
    estimated_lodging_price INTEGER,
    long_stay_discount INTEGER,
    estimated_total_price INTEGER,
    price_basis TEXT,
    availability_status TEXT DEFAULT 'needs_manual_confirmation',
    status TEXT NOT NULL DEFAULT 'new',
    original_message TEXT NOT NULL,
    reply_text TEXT,
    needs_owner_confirmation INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id),
    FOREIGN KEY (contact_id) REFERENCES contacts(id),
    FOREIGN KEY (message_id) REFERENCES messages(id)
);

CREATE TABLE IF NOT EXISTS tenant_operation_state (
    tenant_id INTEGER PRIMARY KEY,
    auto_schedule_enabled INTEGER NOT NULL DEFAULT 1,
    auto_on_start_time TEXT NOT NULL DEFAULT '23:00',
    auto_on_end_time TEXT NOT NULL DEFAULT '08:00',
    manual_mode TEXT,
    manual_set_at TEXT,
    manual_valid_until TEXT,
    last_changed_by_owner_id INTEGER,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id)
);
