# Importing the module registers tables with Base for create_all()
from .core import (  # noqa: F401
    # Enums
    OrderChannel, OrderStatus, PayMode, PrinterType, ChargeMode,
    KOTStatus, StockMoveType, OnlineProvider,

    # Identity & RBAC
    Tenant, Branch, User, Role, Permission, RolePermission, UserRole,

    # Settings / printers / stations
    RestaurantSettings, Printer, KitchenStation,

    # Menu
    MenuCategory, MenuItem, ItemVariant, ModifierGroup, Modifier, ItemModifierGroup,

    # Dining & customers
    DiningTable, Customer,

    # Orders / billing / KOT / tax
    Order, OrderItem, OrderItemModifier, KitchenTicket, KitchenTicketItem,
    Payment, Invoice, TaxRate,

    # Shifts & audit
    Shift, CashMovement, AuditLog,

    # Sync
    SyncEvent, SyncCheckpoint,

    # Inventory
    Ingredient, RecipeBOM, StockMove, Purchase, PurchaseLine,

    # Online orders
    OnlineOrder,
)

# Optional: make star-imports predictable
__all__ = [
    # Enums
    "OrderChannel", "OrderStatus", "PayMode", "PrinterType", "ChargeMode",
    "KOTStatus", "StockMoveType", "OnlineProvider",
    # Identity & RBAC
    "Tenant", "Branch", "User", "Role", "Permission", "RolePermission", "UserRole",
    # Settings / printers / stations
    "RestaurantSettings", "Printer", "KitchenStation",
    # Menu
    "MenuCategory", "MenuItem", "ItemVariant", "ModifierGroup", "Modifier", "ItemModifierGroup",
    # Dining & customers
    "DiningTable", "Customer",
    # Orders / billing / KOT / tax
    "Order", "OrderItem", "OrderItemModifier", "KitchenTicket", "KitchenTicketItem",
    "Payment", "Invoice", "TaxRate",
    # Shifts & audit
    "Shift", "CashMovement", "AuditLog",
    # Sync
    "SyncEvent", "SyncCheckpoint",
    # Inventory
    "Ingredient", "RecipeBOM", "StockMove", "Purchase", "PurchaseLine",
    # Online orders
    "OnlineOrder",
]

all_models = True
