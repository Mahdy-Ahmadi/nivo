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
