# Nivo ORM

Nivo یک **ORM سبک و غیرهمزمان (async)** برای Python است که با استفاده از `aiosqlite` ساخته شده و مدیریت پایگاه داده SQLite را ساده می‌کند.

این کتابخانه برای پروژه‌هایی مناسب است که می‌خواهند از پایگاه داده SQLite به صورت async استفاده کنند و نیازی به ORM های سنگین ندارند.

---

## ویژگی‌ها

- پشتیبانی کامل از **CRUD** (ایجاد، خواندن، بروزرسانی، حذف)
- روابط **یک به چند (ForeignKey)**
- روابط **چند به چند (ManyToMany)**
- async / await
- ساده و سبک
- مدیریت تراکنش‌ها با `atomic()`

---

## نصب

با pip:

```bash
pip install aiosqlite
pip install git+https://github.com/Mahdy-Ahmadi/nivo.git
```

یا اگر فایل `setup.py` دارید:

```bash
python setup.py install
```

---

## استفاده

```python
import asyncio
from nivo import *

# --- Models ---
class Author(Model):
    name = CharField(max_length=100)

class Book(Model):
    title = CharField(max_length=200)
    author = ForeignKey(Author)


async def main():
    # Connect to the database
    db = Connection("library.db")
    await db.connect()

    # Bind models to the database
    Author.bind(db)
    Book.bind(db)

    # Create tables
    await Author.create_table()
    await Book.create_table()

    # Clear previous data (for testing)
    await db.cur.execute("DELETE FROM Author")
    await db.cur.execute("DELETE FROM Book")
    await db.conn.commit()

    # Create an author
    author = await Author.create(name="George Orwell")
    print("✅ Author created:", author.id, author.name)

    # Create books
    await Book.create(title="1984", author=author)
    await Book.create(title="Animal Farm", author=author)
    print("✅ Two books added.")

    # Display all books
    print("\n📚 All books:")
    books = await Book.objects().all()
    for book in books:
        book_author = await book.author
        print(f"- {book.title} (Author: {book_author.name})")

    # Get the first book
    first_book = await Book.objects().first()
    print("\n📖 First book:", first_book.title)

    # Update a book title
    await Book.objects().filter(title="1984").update(title="Nineteen Eighty-Four")
    updated_book = await Book.objects().filter(title="Nineteen Eighty-Four").first()
    print("\n✏️ After update:", updated_book.title)

    # Delete a book
    await Book.objects().filter(title="Animal Farm").delete()
    remaining_books = await Book.objects().all()
    print("\n❌ After deleting 'Animal Farm':")
    for book in remaining_books:
        print("-", book.title)

    # Close the database connection
    await db.close()


if __name__ == "__main__":
    asyncio.run(main())


```

---

## License

MIT License

---

## لینک‌ها

- [سورس پروژه در گیت‌هاب](https://github.com/Mahdy-Ahmadi/nivo)
- [گزارش باگ‌ها](https://github.com/Mahdy-Ahmadi/nivo/issues)