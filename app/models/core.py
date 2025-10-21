from sqlalchemy import (
    String, ForeignKey, Boolean, Numeric, Enum, Text, DateTime, Date, Integer, UniqueConstraint
)
from sqlalchemy.orm import Mapped, mapped_column
from enum import Enum as PyEnum
from datetime import datetime
from app.db import Base
from app.models.common import IdMixin, TSMMixin

# ── Enums ───────────────────────────────────────────────────────────────────
class OrderChannel(PyEnum):
    DINE_IN = "DINE_IN"
    TAKEAWAY = "TAKEAWAY"
    DELIVERY = "DELIVERY"
    ONLINE = "ONLINE"  # aggregator channels map here

class OrderStatus(PyEnum):
    OPEN = "OPEN"
    KITCHEN = "KITCHEN"
    READY = "READY"
    SERVED = "SERVED"
    CLOSED = "CLOSED"
    VOID = "VOID"

class PayMode(PyEnum):
    CASH = "CASH"
    CARD = "CARD"
    UPI = "UPI"
    WALLET = "WALLET"
    COUPON = "COUPON"

class PrinterType(PyEnum):
    BILLING = "BILLING"
    KITCHEN = "KITCHEN"

class ChargeMode(PyEnum):
    NONE = "NONE"
    PERCENT = "PERCENT"
    FLAT = "FLAT"

class KOTStatus(PyEnum):
    NEW = "NEW"
    IN_PROGRESS = "IN_PROGRESS"
    READY = "READY"
    DONE = "DONE"
    CANCELLED = "CANCELLED"

class StockMoveType(PyEnum):
    PURCHASE = "PURCHASE"
    SALE = "SALE"
    ADJUST = "ADJUST"
    WASTAGE = "WASTAGE"

class OnlineProvider(PyEnum):
    ZOMATO = "ZOMATO"
    SWIGGY = "SWIGGY"
    CUSTOM = "CUSTOM"

# Backup targets (for requirement #9)
class BackupProvider(PyEnum):
    NONE = "NONE"
    S3 = "S3"
    GDRIVE = "GDRIVE"
    AZURE = "AZURE"

# ── Onboarding ───────────────────────────────────────────────────────────────
class OnboardProgress(Base, IdMixin, TSMMixin):
    __tablename__ = "onboard_progress"
    tenant_id: Mapped[str] = mapped_column(String(36), unique=True)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    step: Mapped[str | None] = mapped_column(String(40))    # ADMIN | BRANCH | SETTINGS | PRINTERS | FINISH
    last_note: Mapped[str | None] = mapped_column(Text)

# ── Identity ────────────────────────────────────────────────────────────────
class Tenant(Base, IdMixin, TSMMixin):
    __tablename__ = "tenant"
    name: Mapped[str] = mapped_column(String(160))

class Branch(Base, IdMixin, TSMMixin):
    __tablename__ = "branch"
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenant.id"))
    name: Mapped[str] = mapped_column(String(160))
    gstin: Mapped[str | None] = mapped_column(String(32))
    address: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(String(20))
    state_code: Mapped[str | None] = mapped_column(String(2))  # e.g. "MH" for Maharashtra

class User(Base, IdMixin, TSMMixin):
    __tablename__ = "user"
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenant.id"))
    name: Mapped[str] = mapped_column(String(160))
    mobile: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(160))
    pass_hash: Mapped[str] = mapped_column(String(200))
    pin_hash: Mapped[str | None] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

class Role(Base, IdMixin, TSMMixin):
    __tablename__ = "role"
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenant.id"))
    code: Mapped[str] = mapped_column(String(50))

class Permission(Base, IdMixin, TSMMixin):
    __tablename__ = "permission"
    code: Mapped[str] = mapped_column(String(60), unique=True)  # e.g. DISCOUNT, VOID, REPRINT, MANAGER_APPROVE
    description: Mapped[str | None] = mapped_column(Text)

class RolePermission(Base, TSMMixin):
    __tablename__ = "role_permission"
    role_id: Mapped[str] = mapped_column(String(36), ForeignKey("role.id"), primary_key=True)
    permission_id: Mapped[str] = mapped_column(String(36), ForeignKey("permission.id"), primary_key=True)

class UserRole(Base, TSMMixin):
    __tablename__ = "user_role"
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("user.id"), primary_key=True)
    role_id: Mapped[str] = mapped_column(String(36), ForeignKey("role.id"), primary_key=True)

# ── Printers & Stations ─────────────────────────────────────────────────────
class Printer(Base, IdMixin, TSMMixin):
    __tablename__ = "printer"
    tenant_id: Mapped[str] = mapped_column(String(36))
    branch_id: Mapped[str] = mapped_column(String(36))
    name: Mapped[str] = mapped_column(String(120))
    type: Mapped[PrinterType] = mapped_column(Enum(PrinterType))
    connection_url: Mapped[str | None] = mapped_column(String(300))  # local agent webhook or IP:port
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    # Cash drawer support (requirement #10)
    cash_drawer_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    cash_drawer_code: Mapped[str | None] = mapped_column(String(30))  # e.g. "PULSE_2_100"

class KitchenStation(Base, IdMixin, TSMMixin):
    __tablename__ = "kitchen_station"
    tenant_id: Mapped[str] = mapped_column(String(36))
    branch_id: Mapped[str] = mapped_column(String(36))
    name: Mapped[str] = mapped_column(String(120))
    printer_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("printer.id"))

# ── Settings ────────────────────────────────────────────────────────────────
class RestaurantSettings(Base, IdMixin, TSMMixin):
    __tablename__ = "restaurant_settings"
    tenant_id: Mapped[str] = mapped_column(String(36))
    branch_id: Mapped[str] = mapped_column(String(36))
    name: Mapped[str] = mapped_column(String(200))
    logo_url: Mapped[str | None] = mapped_column(String(400))
    address: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(String(20))
    gstin: Mapped[str | None] = mapped_column(String(32))
    fssai: Mapped[str | None] = mapped_column(String(32))
    print_fssai_on_invoice: Mapped[bool] = mapped_column(Boolean, default=False)
    gst_inclusive_default: Mapped[bool] = mapped_column(Boolean, default=True)
    service_charge_mode: Mapped[ChargeMode] = mapped_column(Enum(ChargeMode), default=ChargeMode.NONE)
    service_charge_value: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    packing_charge_mode: Mapped[ChargeMode] = mapped_column(Enum(ChargeMode), default=ChargeMode.NONE)
    packing_charge_value: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    billing_printer_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("printer.id"))
    invoice_footer: Mapped[str | None] = mapped_column(String(200), default="Thank you!")  # footer text

# ── Menu ────────────────────────────────────────────────────────────────────
class MenuCategory(Base, IdMixin, TSMMixin):
    __tablename__ = "menu_category"
    tenant_id: Mapped[str] = mapped_column(String(36))
    branch_id: Mapped[str] = mapped_column(String(36))
    name: Mapped[str] = mapped_column(String(120))
    position: Mapped[int] = mapped_column(default=0)

class MenuItem(Base, IdMixin, TSMMixin):
    __tablename__ = "menu_item"
    tenant_id: Mapped[str] = mapped_column(String(36))
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(Text)  # digital menu
    category_id: Mapped[str] = mapped_column(String(36), ForeignKey("menu_category.id"))
    sku: Mapped[str | None] = mapped_column(String(60))
    hsn: Mapped[str | None] = mapped_column(String(16))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    stock_out: Mapped[bool] = mapped_column(Boolean, default=False)
    tax_inclusive: Mapped[bool] = mapped_column(Boolean, default=True)
    gst_rate: Mapped[float] = mapped_column(Numeric(5, 2), default=5.00)  # default GST%
    kitchen_station_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("kitchen_station.id"))  # route KOT

class ItemVariant(Base, IdMixin, TSMMixin):
    __tablename__ = "item_variant"
    item_id: Mapped[str] = mapped_column(String(36), ForeignKey("menu_item.id"))
    label: Mapped[str] = mapped_column(String(80))
    mrp: Mapped[float | None] = mapped_column(Numeric(10, 2))
    base_price: Mapped[float] = mapped_column(Numeric(10, 2))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)

class ModifierGroup(Base, IdMixin, TSMMixin):
    __tablename__ = "modifier_group"
    tenant_id: Mapped[str] = mapped_column(String(36))
    name: Mapped[str] = mapped_column(String(120))
    min_sel: Mapped[int] = mapped_column(default=0)
    max_sel: Mapped[int | None]
    required: Mapped[bool] = mapped_column(Boolean, default=False)

class Modifier(Base, IdMixin, TSMMixin):
    __tablename__ = "modifier"
    group_id: Mapped[str] = mapped_column(String(36), ForeignKey("modifier_group.id"))
    name: Mapped[str] = mapped_column(String(120))
    price_delta: Mapped[float] = mapped_column(Numeric(10, 2), default=0)

class ItemModifierGroup(Base, TSMMixin):
    __tablename__ = "item_modifier_group"
    item_id: Mapped[str] = mapped_column(String(36), ForeignKey("menu_item.id"), primary_key=True)
    group_id: Mapped[str] = mapped_column(String(36), ForeignKey("modifier_group.id"), primary_key=True)

# ── Dining & customers ──────────────────────────────────────────────────────
class DiningTable(Base, IdMixin, TSMMixin):
    __tablename__ = "dining_table"
    branch_id: Mapped[str] = mapped_column(String(36))
    code: Mapped[str] = mapped_column(String(30), unique=True)
    zone: Mapped[str | None] = mapped_column(String(30))
    seats: Mapped[int | None]

class Customer(Base, IdMixin, TSMMixin):
    __tablename__ = "customer"
    tenant_id: Mapped[str] = mapped_column(String(36))
    name: Mapped[str] = mapped_column(String(160))
    phone: Mapped[str | None] = mapped_column(String(20))
    state_code: Mapped[str | None] = mapped_column(String(2))  # for IGST vs CGST/SGST

# ── Orders / KOT / Payments / Invoice / Tax ─────────────────────────────────
class Order(Base, IdMixin, TSMMixin):
    __tablename__ = "order"
    tenant_id: Mapped[str] = mapped_column(String(36))
    branch_id: Mapped[str] = mapped_column(String(36))
    order_no: Mapped[int]
    channel: Mapped[OrderChannel] = mapped_column(Enum(OrderChannel))
    provider: Mapped[OnlineProvider | None] = mapped_column(Enum(OnlineProvider))
    status: Mapped[OrderStatus] = mapped_column(Enum(OrderStatus), default=OrderStatus.OPEN)
    table_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("dining_table.id"))
    customer_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("customer.id"))
    opened_by_user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("user.id"))  # waiter
    closed_by_user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("user.id"))  # cashier (requirement #5)
    pax: Mapped[int | None]
    source_device_id: Mapped[str | None] = mapped_column(String(36))
    note: Mapped[str | None] = mapped_column(Text)
    opened_at: Mapped[datetime | None]
    closed_at: Mapped[datetime | None]

class OrderItem(Base, IdMixin, TSMMixin):
    __tablename__ = "order_item"
    order_id: Mapped[str] = mapped_column(String(36), ForeignKey("order.id"))
    item_id: Mapped[str] = mapped_column(String(36), ForeignKey("menu_item.id"))
    variant_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("item_variant.id"))
    parent_line_id: Mapped[str | None] = mapped_column(String(36))
    qty: Mapped[float]
    unit_price: Mapped[float] = mapped_column(Numeric(10, 2))
    line_discount: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    gst_rate: Mapped[float] = mapped_column(Numeric(5, 2), default=5.00)
    cgst: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    sgst: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    igst: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    taxable_value: Mapped[float] = mapped_column(Numeric(10, 2), default=0)

class OrderItemModifier(Base, IdMixin, TSMMixin):
    __tablename__ = "order_item_modifier"
    order_item_id: Mapped[str] = mapped_column(String(36), ForeignKey("order_item.id"))
    modifier_id: Mapped[str] = mapped_column(String(36), ForeignKey("modifier.id"))
    qty: Mapped[float] = mapped_column(default=1)
    price_delta: Mapped[float] = mapped_column(Numeric(10, 2), default=0)

class KitchenTicket(Base, IdMixin, TSMMixin):
    __tablename__ = "kitchen_ticket"
    order_id: Mapped[str] = mapped_column(String(36), ForeignKey("order.id"))
    ticket_no: Mapped[int]
    target_station: Mapped[str | None] = mapped_column(String(60))
    status: Mapped[KOTStatus] = mapped_column(Enum(KOTStatus), default=KOTStatus.NEW)
    printed_at: Mapped[datetime | None]
    reprint_count: Mapped[int] = mapped_column(default=0)
    cancel_reason: Mapped[str | None] = mapped_column(Text)

class KitchenTicketItem(Base, IdMixin, TSMMixin):
    __tablename__ = "kitchen_ticket_item"
    ticket_id: Mapped[str] = mapped_column(String(36), ForeignKey("kitchen_ticket.id"))
    order_item_id: Mapped[str] = mapped_column(String(36), ForeignKey("order_item.id"))
    qty: Mapped[float]
    note: Mapped[str | None] = mapped_column(Text)

class Payment(Base, IdMixin, TSMMixin):
    __tablename__ = "payment"
    order_id: Mapped[str] = mapped_column(String(36), ForeignKey("order.id"))
    mode: Mapped[PayMode] = mapped_column(Enum(PayMode))
    amount: Mapped[float] = mapped_column(Numeric(10, 2))
    ref_no: Mapped[str | None] = mapped_column(String(120))
    paid_at: Mapped[datetime | None]

class Invoice(Base, IdMixin, TSMMixin):
    __tablename__ = "invoice"
    order_id: Mapped[str] = mapped_column(String(36), ForeignKey("order.id"))
    invoice_no: Mapped[str] = mapped_column(String(60), unique=True)
    invoice_dt: Mapped[datetime | None]
    place_of_supply: Mapped[str | None] = mapped_column(String(60))
    round_off: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    reprint_count: Mapped[int] = mapped_column(default=0)
    cashier_user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("user.id"))  # for invoice print

class TaxRate(Base, IdMixin, TSMMixin):
    __tablename__ = "tax_rate"
    name: Mapped[str] = mapped_column(String(60))
    cgst: Mapped[float] = mapped_column(Numeric(5, 2))
    sgst: Mapped[float] = mapped_column(Numeric(5, 2))
    igst: Mapped[float] = mapped_column(Numeric(5, 2))

# ── Shifts & audit ──────────────────────────────────────────────────────────
class Shift(Base, IdMixin, TSMMixin):
    __tablename__ = "shift"
    branch_id: Mapped[str] = mapped_column(String(36))
    opened_by: Mapped[str] = mapped_column(String(36))
    opened_at: Mapped[datetime | None]
    closed_by: Mapped[str | None]
    closed_at: Mapped[datetime | None]
    opening_float: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    expected_cash: Mapped[float | None] = mapped_column(Numeric(10, 2))
    actual_cash: Mapped[float | None] = mapped_column(Numeric(10, 2))
    approval_user_id: Mapped[str | None] = mapped_column(String(36))
    close_note: Mapped[str | None] = mapped_column(Text)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)

class CashMovement(Base, IdMixin, TSMMixin):
    __tablename__ = "cash_movement"
    shift_id: Mapped[str] = mapped_column(String(36), ForeignKey("shift.id"))
    kind: Mapped[str] = mapped_column(String(10))  # PAYIN/PAYOUT
    amount: Mapped[float] = mapped_column(Numeric(10, 2))
    reason: Mapped[str | None] = mapped_column(Text)

class AuditLog(Base, IdMixin, TSMMixin):
    __tablename__ = "audit_log"
    actor_user_id: Mapped[str] = mapped_column(String(36))
    entity: Mapped[str] = mapped_column(String(60))
    entity_id: Mapped[str] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(60))
    reason: Mapped[str | None] = mapped_column(Text)  # reason for void/reprint/cancel
    before: Mapped[str | None] = mapped_column(Text)
    after: Mapped[str | None] = mapped_column(Text)

# ── Sync ───────────────────────────────────────────────────────────────────
class SyncEvent(Base, TSMMixin):
    __tablename__ = "sync_event"
    seq: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    entity: Mapped[str] = mapped_column(String(60))
    entity_id: Mapped[str] = mapped_column(String(36))
    op: Mapped[str] = mapped_column(String(10))  # UPSERT/DELETE
    payload: Mapped[str | None] = mapped_column(Text)
    device_id: Mapped[str | None] = mapped_column(String(36))

class SyncCheckpoint(Base, TSMMixin):
    __tablename__ = "sync_checkpoint"
    device_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    last_seq: Mapped[int] = mapped_column(default=0)

# ── Inventory ───────────────────────────────────────────────────────────────
class Ingredient(Base, IdMixin, TSMMixin):
    __tablename__ = "ingredient"
    tenant_id: Mapped[str] = mapped_column(String(36))
    name: Mapped[str] = mapped_column(String(160))
    uom: Mapped[str] = mapped_column(String(20))  # e.g. g, kg, ml, l, pcs
    min_level: Mapped[float] = mapped_column(Numeric(12, 3), default=0)

class RecipeBOM(Base, TSMMixin):
    __tablename__ = "recipe_bom"
    item_id: Mapped[str] = mapped_column(String(36), ForeignKey("menu_item.id"), primary_key=True)
    ingredient_id: Mapped[str] = mapped_column(String(36), ForeignKey("ingredient.id"), primary_key=True)
    qty: Mapped[float] = mapped_column(Numeric(12, 3))

class StockMove(Base, IdMixin, TSMMixin):
    __tablename__ = "stock_move"
    ingredient_id: Mapped[str] = mapped_column(String(36), ForeignKey("ingredient.id"))
    type: Mapped[StockMoveType] = mapped_column(Enum(StockMoveType))
    qty_change: Mapped[float] = mapped_column(Numeric(12, 3))
    reason: Mapped[str | None] = mapped_column(Text)
    ref_order_id: Mapped[str | None] = mapped_column(String(36))
    ref_purchase_id: Mapped[str | None] = mapped_column(String(36))

class Purchase(Base, IdMixin, TSMMixin):
    __tablename__ = "purchase"
    tenant_id: Mapped[str] = mapped_column(String(36))
    supplier: Mapped[str | None] = mapped_column(String(160))
    note: Mapped[str | None] = mapped_column(Text)

class PurchaseLine(Base, IdMixin, TSMMixin):
    __tablename__ = "purchase_line"
    purchase_id: Mapped[str] = mapped_column(String(36), ForeignKey("purchase.id"))
    ingredient_id: Mapped[str] = mapped_column(String(36), ForeignKey("ingredient.id"))
    qty: Mapped[float] = mapped_column(Numeric(12, 3))
    unit_cost: Mapped[float] = mapped_column(Numeric(10, 2))

# ── Online Orders ───────────────────────────────────────────────────────────
class OnlineOrder(Base, IdMixin, TSMMixin):
    __tablename__ = "online_order"
    provider: Mapped[OnlineProvider] = mapped_column(Enum(OnlineProvider))
    provider_order_id: Mapped[str] = mapped_column(String(80))
    order_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("order.id"))
    status: Mapped[str] = mapped_column(String(30), default="RECEIVED")

# ── Backup config & runs (requirement #9) ───────────────────────────────────
class BackupConfig(Base, IdMixin, TSMMixin):
    __tablename__ = "backup_config"
    tenant_id: Mapped[str] = mapped_column(String(36))
    branch_id: Mapped[str] = mapped_column(String(36))
    provider: Mapped[BackupProvider] = mapped_column(Enum(BackupProvider), default=BackupProvider.NONE)
    local_dir: Mapped[str | None] = mapped_column(String(400))
    # generic cloud creds (keep minimal; secure in real prod)
    endpoint: Mapped[str | None] = mapped_column(String(400))   # e.g. S3 endpoint
    bucket: Mapped[str | None] = mapped_column(String(120))
    access_key: Mapped[str | None] = mapped_column(String(200))
    secret_key: Mapped[str | None] = mapped_column(String(200))
    schedule_cron: Mapped[str | None] = mapped_column(String(120))  # "0 3 * * *" daily 3am

class BackupRun(Base, IdMixin, TSMMixin):
    __tablename__ = "backup_run"
    config_id: Mapped[str] = mapped_column(String(36), ForeignKey("backup_config.id"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    bytes_total: Mapped[int | None] = mapped_column(Integer)
    location: Mapped[str | None] = mapped_column(String(400))  # local path or cloud uri
    error: Mapped[str | None] = mapped_column(Text)

# ── Report snapshot tables (requirements #7 & #11) ──────────────────────────
class ReportDailySales(Base, IdMixin, TSMMixin):
    __tablename__ = "report_daily_sales"
    # one row per (date, branch, channel?, provider?) — enforced via unique constraint
    date: Mapped[datetime] = mapped_column(Date)
    tenant_id: Mapped[str] = mapped_column(String(36))
    branch_id: Mapped[str] = mapped_column(String(36))
    channel: Mapped[str | None] = mapped_column(String(20))       # e.g. "DINE_IN"/"ONLINE"/None
    provider: Mapped[str | None] = mapped_column(String(20))      # e.g. "ZOMATO"/"SWIGGY"/None
    orders_count: Mapped[int] = mapped_column(Integer, default=0)
    gross: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    tax: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    cgst: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    sgst: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    igst: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    discounts: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    net: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    __table_args__ = (
        UniqueConstraint("date", "branch_id", "channel", "provider", name="uq_report_daily_sales_key"),
    )

class ReportStockSnapshot(Base, IdMixin, TSMMixin):
    __tablename__ = "report_stock_snapshot"
    at_date: Mapped[datetime] = mapped_column(Date)
    ingredient_id: Mapped[str] = mapped_column(String(36), ForeignKey("ingredient.id"))
    opening_qty: Mapped[float] = mapped_column(Numeric(12, 3), default=0)
    purchased_qty: Mapped[float] = mapped_column(Numeric(12, 3), default=0)
    used_qty: Mapped[float] = mapped_column(Numeric(12, 3), default=0)
    closing_qty: Mapped[float] = mapped_column(Numeric(12, 3), default=0)
    __table_args__ = (
        UniqueConstraint("at_date", "ingredient_id", name="uq_report_stock_snapshot_key"),
    )
