"""Fabricate a plausible ~90-day retail dataset that supports every demo question.

Run:  python -m retail_llm.generate_data
"""
import random
from datetime import datetime, timedelta

from .config import DB_PATH, STORE_OPEN_HOUR, STORE_CLOSE_HOUR, now
from .db import SCHEMA_DDL, connect

SEED = 42
DAYS = 365  # history depth (1 year)

# A full-line hypermarket (think DMart / Reliance Smart + Reliance Digital +
# Trends under one roof). Per category: (min_price, max_price, gross_margin,
# basket_frequency) — frequency is how often an item from this category lands
# in a basket relative to staples; big-ticket categories are rare but huge.
CATEGORIES = {
    # --- daily needs (high frequency, thin margin) ---
    "Fruits & Vegetables":      (15, 400, 0.20, 1.9),
    "Groceries & Staples":      (30, 1600, 0.16, 2.1),
    "Dairy & Eggs":             (20, 500, 0.15, 1.7),
    "Bakery":                   (15, 350, 0.28, 1.2),
    "Beverages":                (20, 900, 0.30, 1.5),
    "Snacks & Packaged Foods":  (10, 600, 0.34, 1.6),
    "Frozen Foods":             (60, 900, 0.24, 0.7),
    "Household Care":            (25, 1400, 0.30, 1.1),
    "Personal Care":            (35, 1800, 0.40, 1.0),
    "Health & Wellness":        (25, 2500, 0.25, 0.5),
    "Baby Care":                (60, 3500, 0.30, 0.35),
    "Pet Care":                 (90, 3000, 0.32, 0.2),
    # --- general merchandise ---
    "Beauty & Cosmetics":       (120, 5000, 0.45, 0.4),
    "Kitchen & Dining":         (120, 8000, 0.35, 0.3),
    "Home & Furnishing":        (150, 30000, 0.42, 0.12),
    "Toys & Games":             (120, 6000, 0.40, 0.28),
    "Books & Stationery":       (20, 2000, 0.35, 0.45),
    "Sports & Fitness":         (150, 18000, 0.38, 0.12),
    "Automotive & Travel":      (120, 6000, 0.35, 0.1),
    # --- apparel & accessories ---
    "Men's Clothing":           (250, 5000, 0.52, 0.45),
    "Women's Clothing":         (300, 7000, 0.54, 0.45),
    "Kids' Clothing":           (150, 2500, 0.50, 0.35),
    "Footwear":                 (300, 8000, 0.46, 0.3),
    "Bags & Luggage":           (400, 12000, 0.45, 0.09),
    "Watches & Accessories":    (500, 20000, 0.50, 0.06),
    # --- electronics (rare, very high value) ---
    "Mobiles & Tablets":        (5000, 160000, 0.09, 0.05),
    "Laptops & Computing":      (22000, 180000, 0.11, 0.025),
    "Televisions":              (12000, 220000, 0.14, 0.02),
    "Large Appliances":         (7000, 110000, 0.17, 0.03),
    "Small Appliances":         (500, 20000, 0.27, 0.09),
    "Electronics Accessories":  (150, 7000, 0.34, 0.6),
}

# Big-ticket lines: quantity is essentially always 1, stock counts are small.
BIG_TICKET = {
    "Mobiles & Tablets", "Laptops & Computing", "Televisions",
    "Large Appliances", "Home & Furnishing",
}

PRODUCT_WORDS = [
    "Classic", "Premium", "Value", "Deluxe", "Everyday", "Select", "Gold",
    "Pro", "Max", "Lite", "Signature", "Essential", "Ultra", "Smart", "Eco",
]
NOUNS = {
    "Fruits & Vegetables": ["Bananas 1kg", "Tomatoes 1kg", "Onions 2kg", "Apples 1kg", "Potatoes 2kg",
                            "Baby Spinach 250g", "Green Capsicum 500g", "Alphonso Mango Box"],
    "Groceries & Staples": ["Basmati Rice 5kg", "Toor Dal 1kg", "Sunflower Oil 5L", "Wheat Atta 10kg",
                            "Sugar 1kg", "Iodised Salt 1kg", "Cashew 500g", "Filter Coffee 500g", "Ghee 1L"],
    "Dairy & Eggs": ["Full Cream Milk 1L", "Toned Milk 1L", "Curd 400g", "Butter 500g", "Cheese Block 400g",
                     "Paneer 200g", "Brown Eggs 12s"],
    "Bakery": ["White Bread", "Multigrain Bread", "Bun Pack 6s", "Butter Croissant", "Chocolate Muffin 4s",
               "Rusk 300g"],
    "Beverages": ["Cola 2L", "Orange Juice 1L", "Green Tea 100s", "Instant Coffee 200g",
                  "Energy Drink 6x250ml", "Packaged Water 12x1L", "Cold Brew 500ml"],
    "Snacks & Packaged Foods": ["Potato Chips 150g", "Salted Peanuts 400g", "Dark Chocolate 100g",
                                "Choco Cookies 600g", "Namkeen Mix 1kg", "Instant Noodles 8s",
                                "Breakfast Cereal 1.2kg", "Peanut Butter 1kg"],
    "Frozen Foods": ["Green Peas 1kg", "Chicken Nuggets 500g", "French Fries 1.25kg", "Mixed Veg 1kg",
                     "Vanilla Ice Cream 1L", "Frozen Paratha 15s"],
    "Household Care": ["Dishwash Gel 1L", "Floor Cleaner 2L", "Detergent Powder 4kg", "Liquid Detergent 2L",
                       "Garbage Bags 60s", "Toilet Cleaner 1L", "Mosquito Repellent Refill"],
    "Personal Care": ["Shampoo 650ml", "Toothpaste 300g", "Bath Soap 6x100g", "Face Wash 150ml",
                      "Deodorant 220ml", "Shaving Foam 300ml", "Sanitary Pads 30s", "Hand Wash 750ml"],
    "Health & Wellness": ["Multivitamin 60s", "Protein Powder 1kg", "Antiseptic Liquid 500ml",
                          "Digital Thermometer", "First Aid Kit", "N95 Masks 20s", "Glucometer Strips 50s"],
    "Baby Care": ["Diapers L 64s", "Baby Wipes 3x72s", "Infant Formula 400g", "Baby Lotion 400ml",
                  "Baby Food Jar 6s"],
    "Pet Care": ["Dog Food 3kg", "Cat Food 1.2kg", "Cat Litter 5kg", "Chew Treats 200g", "Pet Shampoo 500ml"],
    "Beauty & Cosmetics": ["Liquid Foundation", "Matte Lipstick", "Kajal Twin Pack", "Compact Powder",
                           "Eau de Parfum 100ml", "Sunscreen SPF50", "Hair Serum 100ml", "Nail Polish Set"],
    "Kitchen & Dining": ["Pressure Cooker 5L", "Non-stick Tawa 28cm", "Dinner Set 32pc", "Steel Water Bottle 1L",
                         "Chef Knife 8in", "Storage Container Set 12pc", "Casserole 2.5L"],
    "Home & Furnishing": ["Double Bedsheet Set", "Memory Foam Pillow 2s", "Cotton Bath Towel",
                          "Blackout Curtains 2s", "Study Table", "3-Seater Sofa", "Wall Shelf Set",
                          "Floor Rug 5x7"],
    "Toys & Games": ["Building Blocks 500pc", "Remote Control Car", "Board Game", "Soft Teddy 60cm",
                     "Puzzle 1000pc", "Kids Cycle 16in"],
    "Books & Stationery": ["A4 Copier Paper 500s", "Gel Pen 10s", "Spiral Notebook 300pg", "Stapler + Pins",
                           "Fiction Bestseller", "Kids Colouring Kit", "Sticky Notes 5s"],
    "Sports & Fitness": ["Yoga Mat 6mm", "Adjustable Dumbbell 20kg", "Cricket Bat", "Football Size 5",
                         "Skipping Rope", "Treadmill", "Badminton Racket Set"],
    "Automotive & Travel": ["Car Vacuum Cleaner", "Engine Oil 3.5L", "Dashboard Polish 500ml",
                            "Tyre Inflator", "Travel Neck Pillow", "Jump Start Cables"],
    "Men's Clothing": ["Cotton Formal Shirt", "Slim Fit Jeans", "Round Neck T-Shirt 2s", "Chino Trousers",
                       "Polo T-Shirt", "Full Sleeve Sweatshirt", "Ethnic Kurta"],
    "Women's Clothing": ["Rayon Kurti", "High Rise Jeans", "Cotton Leggings 2s", "A-line Dress",
                         "Printed Saree", "Knit Cardigan", "Palazzo Pants"],
    "Kids' Clothing": ["Boys T-Shirt 3s", "Girls Frock", "Kids Track Pants 2s", "Infant Romper Set",
                       "School Uniform Set"],
    "Footwear": ["Running Shoes", "Casual Sneakers", "Leather Formal Shoes", "Women's Wedges",
                 "Kids School Shoes", "Flip Flops 2s", "Sports Sandals"],
    "Bags & Luggage": ["Cabin Trolley 55cm", "Check-in Trolley 65cm", "Laptop Backpack", "Tote Bag",
                       "Duffel Bag 40L", "School Bag"],
    "Watches & Accessories": ["Analog Wrist Watch", "Smart Watch", "Leather Belt", "Sunglasses UV400",
                              "Silver Pendant Set", "Wallet + Card Holder"],
    "Mobiles & Tablets": ["5G Smartphone 128GB", "5G Smartphone 256GB", "Budget Smartphone 64GB",
                          "Android Tablet 11in", "Flagship Smartphone 512GB", "Feature Phone"],
    "Laptops & Computing": ["14in Thin Laptop i5", "15in Laptop i7 16GB", "2-in-1 Convertible Laptop",
                            "Gaming Laptop RTX", "Wireless Keyboard + Mouse", "27in Monitor"],
    "Televisions": ["32in HD LED TV", "43in FHD Smart TV", "55in 4K UHD Smart TV", "65in 4K QLED TV",
                    "Soundbar 2.1ch"],
    "Large Appliances": ["Double Door Refrigerator 260L", "Front Load Washing Machine 7kg",
                         "1.5T Split AC 3-Star", "Dishwasher 13 Place", "Chest Freezer 200L"],
    "Small Appliances": ["Mixer Grinder 750W", "Microwave Oven 20L", "Air Fryer 4L", "Electric Kettle 1.5L",
                         "Induction Cooktop", "Steam Iron", "Vacuum Cleaner 1400W", "Room Heater"],
    "Electronics Accessories": ["USB-C Cable 1m", "65W Fast Charger", "TWS Earbuds", "Power Bank 20000mAh",
                                "AA Batteries 8s", "HDMI Cable 2m", "Phone Case", "32GB Pen Drive"],
}

FIRST_NAMES = ["Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Sai", "Reyansh", "Ayaan", "Krishna", "Ishaan",
               "Ananya", "Diya", "Aadhya", "Saanvi", "Pari", "Anika", "Navya", "Riya", "Myra", "Sara",
               "Rohan", "Kabir", "Dev", "Neha", "Priya", "Karan", "Meera", "Nikhil", "Pooja", "Rahul"]
LAST_NAMES = ["Sharma", "Verma", "Gupta", "Iyer", "Nair", "Reddy", "Patel", "Mehta", "Rao", "Singh",
              "Khan", "Das", "Bose", "Kapoor", "Menon", "Pillai", "Chopra", "Bhat", "Joshi", "Kulkarni"]

EXCEPTION_TYPES = ["VOID_WITHOUT_SCAN", "HIGH_DISCOUNT", "MANUAL_PRICE_OVERRIDE", "NO_SALE_OPEN"]

# Relative weight of footfall / sales by hour of day (index 0 == STORE_OPEN_HOUR).
HOUR_WEIGHTS = [0.5, 0.8, 1.0, 1.3, 1.1, 0.7, 0.6, 0.9, 1.4, 1.6, 1.2, 0.6]
# Relative weight by weekday (Mon=0 .. Sun=6).
DOW_WEIGHTS = [0.8, 0.8, 0.9, 1.0, 1.3, 1.7, 1.5]


def _rng():
    return random.Random(SEED)


def _name(r):
    return f"{r.choice(FIRST_NAMES)} {r.choice(LAST_NAMES)}"


def build():
    r = _rng()
    end = now().replace(minute=0, second=0, microsecond=0)
    start_day = (end - timedelta(days=DAYS)).date()

    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = connect()
    conn.executescript(SCHEMA_DDL)

    # ---- staff -------------------------------------------------------------
    staff = []
    cashier_names = ["Priya Nair", "Rahul Verma", "Sneha Iyer", "Amit Gupta", "Divya Rao",
                     "Karan Singh", "Farah Khan", "Manish Das"]
    for i, nm in enumerate(cashier_names, start=1):
        staff.append((i, nm, "CASHIER", str(start_day - timedelta(days=r.randint(30, 900)))))
    staff.append((len(staff) + 1, "Sunil Mehta", "MANAGER", str(start_day - timedelta(days=1200))))
    for i in range(2):
        sid = len(staff) + 1
        staff.append((sid, _name(r), "FLOOR", str(start_day - timedelta(days=r.randint(30, 600)))))
    conn.executemany("INSERT INTO staff VALUES (?,?,?,?)", staff)
    cashier_ids = [s[0] for s in staff if s[2] == "CASHIER"]
    # per-cashier speed profile (mean billing seconds)
    cashier_speed = {cid: r.uniform(35, 70) for cid in cashier_ids}

    # ---- products --------------------------------------------------------
    products = []
    pid = 0
    for cat, (lo, hi, margin, freq) in CATEGORIES.items():
        big = cat in BIG_TICKET
        for noun in NOUNS[cat]:
            for _ in range(1 if big else r.choices([1, 2], weights=[3, 2])[0]):
                pid += 1
                word = r.choice(PRODUCT_WORDS)
                # skew price toward the low end (a right tail of premium SKUs)
                price = round(lo + (hi - lo) * (r.random() ** 2.2), 2)
                cost = round(price * (1 - margin) * r.uniform(0.9, 1.05), 2)
                added = start_day - timedelta(days=r.randint(0, 400))
                thr = r.choice([2, 3, 4] if big else [8, 10, 12, 15, 20, 25])
                products.append({
                    "product_id": pid, "name": f"{word} {noun}", "category": cat,
                    "price": price, "cost": cost,
                    "reorder_threshold": thr,
                    "date_added": str(added),
                    "freq": freq,
                    "big": big,
                    "popularity": r.random() ** 2,  # skew: few hot sellers
                })
    # a handful of guaranteed dead-stock items (added long ago, never sell well)
    dead_ids = set(r.sample([p["product_id"] for p in products], 15))
    for p in products:
        if p["product_id"] in dead_ids:
            p["popularity"] = 0.0

    # ---- customers ------------------------------------------------------
    customers = []
    n_customers = 850
    for cid in range(1, n_customers + 1):
        fv = end - timedelta(days=r.randint(1, DAYS), hours=r.randint(0, 200))
        customers.append({
            "customer_id": cid, "name": _name(r),
            "phone": f"9{r.randint(100000000, 999999999)}",
            "first_visit": fv, "last_visit": fv, "visit_count": 0, "total_spend": 0.0,
            "loyalty": r.random(),  # higher -> visits more often
        })
    # ensure customer #1234-style lookups work: pad to >=1300
    for cid in range(n_customers + 1, 1301):
        fv = end - timedelta(days=r.randint(1, DAYS))
        customers.append({"customer_id": cid, "name": _name(r), "phone": f"9{r.randint(100000000,999999999)}",
                          "first_visit": fv, "last_visit": fv, "visit_count": 0, "total_spend": 0.0,
                          "loyalty": r.random()})

    prod_by_id = {p["product_id"]: p for p in products}
    # dead-stock items get a true zero weight so they genuinely never sell;
    # otherwise weight = intrinsic popularity scaled by the category frequency
    pop_weights = [0.0 if p["product_id"] in dead_ids
                   else (p["popularity"] + 0.05) * p["freq"]
                   for p in products]

    units_sold = {p["product_id"]: 0 for p in products}
    last_sold = {p["product_id"]: None for p in products}

    tx_rows = []
    item_rows = []
    footfall_rows = []
    tx_id = 0

    for d in range(DAYS + 1):
        day = end.date() - timedelta(days=DAYS - d)
        dow = day.weekday()
        day_factor = DOW_WEIGHTS[dow] * r.uniform(0.85, 1.15)
        # slight upward trend over time
        trend = 0.8 + 0.4 * (d / DAYS)

        for hi, hour in enumerate(range(STORE_OPEN_HOUR, STORE_CLOSE_HOUR)):
            hour_factor = HOUR_WEIGHTS[hi] * day_factor * trend
            # footfall for this hour
            fcount = max(0, int(r.gauss(22 * hour_factor, 6)))
            footfall_rows.append((1, datetime(day.year, day.month, day.day, hour).isoformat(), fcount))

            # ~35% of footfall converts to a bill
            n_bills = max(0, int(r.gauss(fcount * 0.35, 3)))
            for _ in range(n_bills):
                bt = datetime(day.year, day.month, day.day, hour,
                              r.randint(0, 59), r.randint(0, 59))
                if bt > end:
                    continue
                cashier = r.choice(cashier_ids)
                secs = max(12, int(r.gauss(cashier_speed[cashier], cashier_speed[cashier] * 0.35)))
                if r.random() < 0.03:
                    secs += r.randint(120, 400)  # outlier

                # basket
                n_lines = r.choices([1, 2, 3, 4, 5, 6, 8], weights=[20, 25, 20, 14, 10, 7, 4])[0]
                chosen = r.choices(products, weights=pop_weights, k=n_lines)
                subtotal = 0.0
                tx_id += 1
                seen = set()
                for prod in chosen:
                    if prod["product_id"] in seen:
                        continue
                    seen.add(prod["product_id"])
                    if prod["big"] or prod["price"] > 3000:
                        qty = 1
                    else:
                        qty = r.choices([1, 2, 3, 4], weights=[70, 20, 7, 3])[0]
                    up = prod["price"]
                    lt = round(up * qty, 2)
                    subtotal += lt
                    item_rows.append((tx_id, prod["product_id"], qty, up, lt))
                    units_sold[prod["product_id"]] += qty
                    last_sold[prod["product_id"]] = day

                # customer (repeat-visit skew)
                if r.random() < 0.75:
                    cust = r.choices(customers, weights=[c["loyalty"] + 0.05 for c in customers])[0]
                    cust_id = cust["customer_id"]
                    cust["visit_count"] += 1
                    cust["last_visit"] = bt
                    if bt < cust["first_visit"]:
                        cust["first_visit"] = bt
                else:
                    cust_id = None

                discount = 0.0
                is_exc, exc_type = 0, None
                roll = r.random()
                if roll < 0.02:
                    is_exc = 1
                    exc_type = r.choice(EXCEPTION_TYPES)
                    if exc_type == "HIGH_DISCOUNT":
                        discount = round(r.uniform(25, 55), 1)
                elif roll < 0.10:
                    discount = round(r.choice([2, 5, 5, 10, 10, 15]), 1)

                total = round(subtotal * (1 - discount / 100), 2)
                if cust_id is not None:
                    cust["total_spend"] += total
                start_t = bt - timedelta(seconds=secs)
                tx_rows.append((tx_id, bt.isoformat(), cashier, cust_id, round(subtotal, 2),
                                discount, total, is_exc, exc_type,
                                start_t.isoformat(), bt.isoformat(), secs))

    # ---- current stock: base + noise, some below threshold, dead stock high
    prod_insert = []
    for p in products:
        sold = units_sold[p["product_id"]]
        if p["product_id"] in dead_ids:
            stock = r.randint(40, 120)
        else:
            # restocked periodically; leave ~15% of catalogue below threshold
            stock = max(0, int(r.gauss(p["reorder_threshold"] * r.uniform(0.4, 6.0), 8)))
        ls = last_sold[p["product_id"]]
        prod_insert.append((p["product_id"], p["name"], p["category"], p["price"], p["cost"],
                            p["reorder_threshold"], stock, p["date_added"],
                            str(ls) if ls else None))
    conn.executemany("INSERT INTO products VALUES (?,?,?,?,?,?,?,?,?)", prod_insert)

    cust_insert = [(c["customer_id"], c["name"], c["phone"],
                    c["first_visit"].isoformat() if isinstance(c["first_visit"], datetime) else c["first_visit"],
                    c["last_visit"].isoformat() if isinstance(c["last_visit"], datetime) else c["last_visit"],
                    c["visit_count"], round(c["total_spend"], 2)) for c in customers]
    conn.executemany("INSERT INTO customers VALUES (?,?,?,?,?,?,?)", cust_insert)

    conn.executemany("INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", tx_rows)
    conn.executemany(
        "INSERT INTO transaction_items(transaction_id,product_id,quantity,unit_price,line_total) VALUES (?,?,?,?,?)",
        item_rows)
    conn.executemany("INSERT INTO footfall(store_id,ts,count) VALUES (?,?,?)", footfall_rows)

    conn.commit()
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ["products", "staff", "customers", "transactions", "transaction_items", "footfall"]}
    conn.close()
    return counts


if __name__ == "__main__":
    c = build()
    print(f"Built {DB_PATH}")
    for k, v in c.items():
        print(f"  {k:20s} {v:>8,d}")
