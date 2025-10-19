from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy.orm import Session
from app.models.core import OrderItem, RestaurantSettings

def _money(x) -> float:
    return float(Decimal(x).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))

def compute_bill(db: Session, order_id: str) -> dict:
    rs = db.query(RestaurantSettings).first()
    gst_inclusive_default = bool(rs.gst_inclusive_default) if rs else True

    lines = db.query(OrderItem).filter(OrderItem.order_id == order_id).all()
    subtotal = 0.0
    tax_total = 0.0

    for l in lines:
        base = float(l.qty) * float(l.unit_price) - float(l.line_discount or 0)
        inclusive = gst_inclusive_default  # could be per-item later
        if inclusive:
            taxable = base / (1 + float(l.gst_rate)/100)
            tax = base - taxable
            subtotal += taxable
        else:
            taxable = base
            tax = base * float(l.gst_rate)/100
            subtotal += base
        tax_total += tax

    service = packing = 0.0
    if rs:
        if rs.service_charge_mode.name == 'PERCENT':
            service = subtotal * float(rs.service_charge_value)/100
        elif rs.service_charge_mode.name == 'FLAT':
            service = float(rs.service_charge_value)
        if rs.packing_charge_mode.name == 'PERCENT':
            packing = subtotal * float(rs.packing_charge_value)/100
        elif rs.packing_charge_mode.name == 'FLAT':
            packing = float(rs.packing_charge_value)

    gross = subtotal + tax_total + service + packing
    rounded_total = float(Decimal(gross).quantize(Decimal('1'), rounding=ROUND_HALF_UP))  # nearest ₹1
    round_off = rounded_total - gross

    cgst = tax_total / 2.0
    sgst = tax_total / 2.0

    return {
        "subtotal": _money(subtotal),
        "tax": _money(tax_total),
        "cgst": _money(cgst),
        "sgst": _money(sgst),
        "service": _money(service),
        "packing": _money(packing),
        "round_off": _money(round_off),
        "total": _money(rounded_total),
    }
