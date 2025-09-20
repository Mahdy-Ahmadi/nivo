from typing import Any, Type
from .db import DoesNotExist, MultipleObjectsReturned
class ModelMeta(type):
    def __new__(cls, name, bases, attrs):
        if name == "Model":
            return super().__new__(cls, name, bases, attrs)
            
        fields = {}
        m2m_fields = {}
        
        # اگر کاربر فیلد id تعریف نکرده باشد، یک AutoField اضافه می‌کنیم
        if 'id' not in attrs:
            attrs['id'] = AutoField(primary_key=True)

        for key, value in list(attrs.items()):
            if isinstance(value, Field):
                value.name = key
                fields[key] = value
                if isinstance(value, ForeignKey):
                    attrs[f"{key}_id"] = IntegerField(null=value.null)
                    fields[f"{key}_id"] = attrs[f"{key}_id"]
                    fields[f"{key}_id"].name = f"{key}_id"
            elif isinstance(value, ManyToManyField):
                value.name = key
                m2m_fields[key] = value


        attrs["_fields"] = fields
        attrs["_m2m_fields"] = m2m_fields
        attrs["_table_name"] = name.lower()
        
        new_class = super().__new__(cls, name, bases, attrs)
        
        # مدل را به فیلدها متصل می‌کنیم
        for field in new_class._fields.values():
            field.model = new_class
        for m2m_field in new_class._m2m_fields.values():
            m2m_field.model = new_class

        return new_class
class Model(metaclass=ModelMeta):
    _db: "Database" = None
    DoesNotExist = DoesNotExist
    MultipleObjectsReturned = MultipleObjectsReturned

    @classmethod
    def bind(cls, db: "Database"):
        cls._db = db

    @classmethod
    async def create_table(cls):
        fields_defs = []
        foreign_keys = []
        for name, field in cls._fields.items():
            if isinstance(field, ForeignKey):
                # فیلد واقعی در دیتابیس `_id` دارد
                db_col_name = f"{field.name}_id"
                s = f"{db_col_name} {field.field_type}"
                foreign_keys.append(f"FOREIGN KEY ({db_col_name}) REFERENCES {field.to._table_name}(id) ON DELETE {field.on_delete}")
            else:
                s = f"{name} {field.field_type}"

            if field.primary_key:
                s += " PRIMARY KEY"
                if isinstance(field, AutoField):
                    s += " AUTOINCREMENT"
            if field.unique:
                s += " UNIQUE"
            if not field.null:
                s += " NOT NULL"
            if field.default is not None:
                default_val = field.default
                if isinstance(default_val, str):
                    default_val = f"'{default_val}'"
                s += f" DEFAULT {default_val}"

            # برای ForeignKey ها تعریف ستون اصلی را اضافه نمیکنیم چون _id آن را اضافه کرده
            if not isinstance(field, ForeignKey):
                fields_defs.append(s)

        query = f"CREATE TABLE IF NOT EXISTS {cls._table_name} ({', '.join(fields_defs + foreign_keys)})"
        await cls._db.cur.execute(query)
        
        # ساخت جداول ManyToMany
        for name, m2m in cls._m2m_fields.items():
            table_name = f"{cls._table_name}_{name}_{m2m.to._table_name}"
            from_fk = f"{cls._table_name}_id"
            to_fk = f"{m2m.to._table_name}_id"
            
            m2m_query = f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                {from_fk} INTEGER,
                {to_fk} INTEGER,
                PRIMARY KEY ({from_fk}, {to_fk}),
                FOREIGN KEY ({from_fk}) REFERENCES {cls._table_name}(id) ON DELETE CASCADE,
                FOREIGN KEY ({to_fk}) REFERENCES {m2m.to._table_name}(id) ON DELETE CASCADE
            )
            """
            await cls._db.cur.execute(m2m_query)

        if cls._db.auto_commit:
            await cls._db.conn.commit()

    @classmethod
    def objects(cls):
        return QuerySet(cls)

    @classmethod
    async def create(cls, **kwargs):
        obj = cls(**kwargs)
        await obj.save()
        return obj

    def __init__(self, **kwargs):
        # مقداردهی اولیه فیلدها
        for name, field in self._fields.items():
            if not isinstance(field, ForeignKey): # ForeignKey ها توسط __set__ خودشان مدیریت می‌شوند
                value = kwargs.get(name, field.default)
                setattr(self, name, field.to_python(value))
        
        # مقداردهی اولیه روابط
        for name, field in self._fields.items():
            if isinstance(field, ForeignKey):
                # اگر آبجکت پاس داده شده باشد
                if name in kwargs:
                    setattr(self, name, kwargs[name])
                # اگر فقط _id پاس داده شده باشد
                elif f"{name}_id" in kwargs:
                     setattr(self, f"{name}_id", kwargs[f"{name}_id"])

        # مقداردهی اولیه منیجرهای M2M
        for name, m2m_field in self._m2m_fields.items():
            through_table = f"{self._table_name}_{name}_{m2m_field.to._table_name}"
            manager = ManyToManyManager(self, m2m_field.to, through_table)
            setattr(self, name, manager)

    async def save(self):
        pk_val = getattr(self, 'id', None)
        is_new = pk_val is None

        data = {}
        for name, field in self._fields.items():
            if not isinstance(field, ForeignKey) and not (isinstance(field, AutoField) and is_new):
                data[name] = field.to_db(getattr(self, name))

        if is_new:
            # INSERT
            keys = ", ".join(data.keys())
            placeholders = ", ".join("?" for _ in data)
            query = f"INSERT INTO {self._table_name} ({keys}) VALUES ({placeholders})"
            async with self._db._lock:
                await self._db.cur.execute(query, tuple(data.values()))
                self.id = self._db.cur.lastrowid # مقدار id جدید را ست می‌کنیم
                if self._db.auto_commit: await self._db.conn.commit()
        else:
            # UPDATE
            set_clause = ", ".join(f"{k} = ?" for k in data)
            query = f"UPDATE {self._table_name} SET {set_clause} WHERE id = ?"
            async with self._db._lock:
                await self._db.cur.execute(query, (*data.values(), pk_val))
                if self._db.auto_commit: await self._db.conn.commit()
        
    async def delete(self):
        pk_val = getattr(self, 'id', None)
        if pk_val is None: return
        query = f"DELETE FROM {self._table_name} WHERE id = ?"
        await self._db.cur.execute(query, (pk_val,))
        if self._db.auto_commit: await self._db.conn.commit()

    def __repr__(self):
        return f"<{self.__class__.__name__}: {getattr(self, 'id', 'Unsaved')}>"
class Field:
    def __init__(self, field_type: str, primary_key=False, unique=False, null=True, default=None, max_length=None):
        self.field_type = field_type
        self.primary_key = primary_key
        self.unique = unique
        self.null = null
        self.default = default
        self.name = None
        self.model = None
        self.max_length = max_length

    def to_db(self, value: Any) -> Any: return value
    def to_python(self, value: Any) -> Any: return value

class AutoField(Field):
    def __init__(self, primary_key=True):
        super().__init__("INTEGER", primary_key=primary_key, null=False)

class IntegerField(Field):
    def __init__(self, primary_key=False, unique=False, null=True, default=None):
        super().__init__("INTEGER", primary_key, unique, null, default)

class CharField(Field):
    def __init__(self, max_length=255, unique=False, null=True, default=None):
        super().__init__("TEXT", False, unique, null, default, max_length)

class BooleanField(Field):
    def __init__(self, default=False, null=False):
        super().__init__("INTEGER", False, False, null, int(default))

    def to_db(self, value: bool) -> int: return int(value)
    def to_python(self, value: int) -> bool: return bool(value)

class FloatField(Field):
    def __init__(self, default=0.0, null=True): super().__init__("REAL", False, False, null, default)

class DateTimeField(Field):
    def __init__(self, default=None, null=True): super().__init__("TEXT", False, False, null, default)

class ForeignKey(Field):
    def __init__(self, to: Type[Model], null=True, on_delete="CASCADE"):
        super().__init__("INTEGER", null=null)
        self.to = to
        self.on_delete = on_delete.upper()
        self._related_instance_cache = None

    def __get__(self, instance, owner):
        if instance is None:
            return self
        
        # برگرداندن یک awaitable برای گرفتن آبجکت مرتبط
        async def _get_related():
            if not hasattr(instance, f"{self.name}_id"):
                 return None
            related_id = getattr(instance, f"{self.name}_id")
            if related_id is None:
                return None
            if self._related_instance_cache is None:
                self._related_instance_cache = await self.to.objects.get(id=related_id)
            return self._related_instance_cache
        return _get_related()

    def __set__(self, instance, value):
        if isinstance(value, self.to):
            setattr(instance, f"{self.name}_id", value.id)
            self._related_instance_cache = value
        elif value is None:
            setattr(instance, f"{self.name}_id", None)
            self._related_instance_cache = None
        else:
             raise TypeError(f"Expected an instance of {self.to.__name__}, but got {type(value).__name__}")

class ManyToManyField:
    def __init__(self, to: Type[Model]):
        self.to = to
        self.name = None
        self.model = None

from .db import QuerySet

from typing import Any, Dict, List, Optional, Type, Tuple, Union, AsyncGenerator
class IntegerField(Field):
    def __init__(self, primary_key=False, unique=False, null=True, default=None):
        super().__init__("INTEGER", primary_key, unique, null, default)


class ManyToManyManager:
    """کلاس منیجر برای مدیریت روابط چند-به-چند."""
    def __init__(self, model_instance: "Model", to_model: Type["Model"], through_table: str):
        self.instance = model_instance
        self.to_model = to_model
        self.db = model_instance._db
        
        self.from_model_name = self.instance.__class__.__name__.lower()
        self.to_model_name = self.to_model.__name__.lower()
        self.through_table = through_table

    async def add(self, *related_objs):
        """اضافه کردن یک یا چند آبجکت به رابطه."""
        if not related_objs: return
        query = f"""
            INSERT OR IGNORE INTO {self.through_table} ({self.from_model_name}_id, {self.to_model_name}_id)
            VALUES (?, ?)
        """
        values = [(self.instance.id, obj.id) for obj in related_objs]
        async with self.db._lock:
            await self.db.cur.executemany(query, values)
            if self.db.auto_commit: await self.db.conn.commit()

    async def remove(self, *related_objs):
        """حذف کردن یک یا چند آبجکت از رابطه."""
        if not related_objs: return
        ids_to_remove = [obj.id for obj in related_objs]
        placeholders = ','.join('?' for _ in ids_to_remove)
        query = f"""
            DELETE FROM {self.through_table}
            WHERE {self.from_model_name}_id = ? AND {self.to_model_name}_id IN ({placeholders})
        """
        async with self.db._lock:
            await self.db.cur.execute(query, (self.instance.id, *ids_to_remove))
            if self.db.auto_commit: await self.db.conn.commit()

    async def clear(self):
        """حذف تمام روابط برای این آبجکت."""
        query = f"DELETE FROM {self.through_table} WHERE {self.from_model_name}_id = ?"
        async with self.db._lock:
            await self.db.cur.execute(query, (self.instance.id,))
            if self.db.auto_commit: await self.db.conn.commit()

    async def set(self, related_objs):
        """تنظیم دقیق روابط (حذف قبلی‌ها و اضافه کردن جدیدها)."""
        async with self.db.atomic():
            await self.clear()
            await self.add(*related_objs)

    def __aiter__(self):
        """اجازه می‌دهد تا روی آبجکت‌های مرتبط پیمایش کنیم (async for)."""
        return self._get_related_objects_generator()

    async def _get_related_objects_generator(self) -> AsyncGenerator["Model", None]:
        rows = await self.all()
        for row in rows:
            yield row
            
    async def all(self):
        """گرفتن لیستی از تمام آبجکت‌های مرتبط."""
        query = f"""
            SELECT t.* FROM {self.to_model_name} t
            INNER JOIN {self.through_table} th ON t.id = th.{self.to_model_name}_id
            WHERE th.{self.from_model_name}_id = ?
        """
        async with self.db._lock:
            await self.db.cur.execute(query, (self.instance.id,))
            rows = await self.db.cur.fetchall()
            return [self.to_model(**dict(r)) for r in rows]
