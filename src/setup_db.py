import sqlite3

def create_sample_db():
    conn = sqlite3.connect("company_sales.db")
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS employees (
        emp_id INTEGER PRIMARY KEY,
        name TEXT,
        department TEXT,
        hire_year INTEGER
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sales (
        sale_id INTEGER PRIMARY KEY,
        emp_id INTEGER,
        product TEXT,
        amount REAL,
        month TEXT,
        FOREIGN KEY(emp_id) REFERENCES employees(emp_id)
    )
    """)

    cursor.execute("DELETE FROM employees")
    cursor.execute("DELETE FROM sales")

    employees = [
        (1, "Ahmed Hassan", "Electronics", 2022),
        (2, "Sara Mahmoud", "Electronics", 2023),
        (3, "Omar Khaled", "Software", 2021),
        (4, "Nour El-Din", "Software", 2024),
    ]
    cursor.executemany("INSERT INTO employees VALUES (?, ?, ?, ?)", employees)

    sales = [
        (101, 1, "Laptop Pro", 15000, "2026-01"),
        (102, 2, "Smartphone X", 8000, "2026-01"),
        (103, 3, "Cloud License", 12000, "2026-01"),
        (104, 1, "Laptop Pro", 18000, "2026-02"),
        (105, 4, "AI Suite", 9500, "2026-02"),
        (106, 2, "Tablet Air", 6000, "2026-02"),
        (107, 3, "Cloud License", 22000, "2026-03"),
        (108, 1, "Monitor 4K", 7500, "2026-03"),
        (109, 4, "AI Suite", 14000, "2026-03"),
        (110, 2, "Smartphone X", 11000, "2026-03"),
    ]
    cursor.executemany("INSERT INTO sales VALUES (?, ?, ?, ?, ?)", sales)

    conn.commit()
    conn.close()
    print("Database 'company_sales.db' created successfully!")

if __name__ == "__main__":
    create_sample_db()
