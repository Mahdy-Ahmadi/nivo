import aiosqlite
import asyncio
from typing import Any, Dict, List, Optional, Type, Tuple, Union, AsyncGenerator
from contextlib import asynccontextmanager
import inspect


class DoesNotExist(Exception):
    """Raised when an object is not found in the database."""
    pass

class MultipleObjectsReturned(Exception):
    """Raised when a query returns multiple objects but only one was expected."""
    pass


class Field:
    """پایه تمام فیلدها. وظیفه تعریف نوع ستون در دیتابیس را دارد."""
    def __init__(self, field_type: str, primary_key=False, unique=False, null=True, default=None, max_length=None):
        self.field_type = field_type
        self.primary_key = primary_key
        self.unique = unique
        self.null = null
        self.default = default
        self.name = None
        self.model = None 
        self.max_length = max_length

    def to_db(self, value: Any) -> Any:
        """مقدار پایتونی را برای ذخیره در دیتابیس تبدیل می‌کند."""
        return value

    def to_python(self, value: Any) -> Any:
        """مقدار دیتابیسی را به نوع پایتونی مناسب تبدیل می‌کند."""
        return value

class AutoField(Field):
    """فیلد کلید اصلی با افزایش خودکار."""
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

    def to_db(self, value: bool) -> int:
        return int(value)

    def to_python(self, value: int) -> bool:
        return bool(value)

class FloatField(Field):
    def __init__(self, default=0.0, null=True):
        super().__init__("REAL", False, False, null, default)

class DateTimeField(Field):
    """
    توجه: برای سادگی، این فیلد همچنان از TEXT استفاده می‌کند.
    برای کارایی بهتر می‌توان از ISO 8601 format string استفاده کرد.
    """
    def __init__(self, default=None, null=True):
        super().__init__("TEXT", False, False, null, default)

class ForeignKey(Field):
    """فیلد کلید خارجی برای روابط یک-به-بسیاری."""
    def __init__(self, to: Type["Model"], null=True, on_delete="CASCADE"):
        super().__init__("INTEGER", null=null)
        self.to = to
        self.on_delete = on_delete.upper() 
        self._related_instance_cache = None

    def __get__(self, instance, owner):
        if instance is None:
            return self
        
        
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

class ManyToManyField:
    def __init__(self, to: Type["Model"]):
        self.to = to
        self.name = None
        self.model = None


class ModelMeta(type):
    def __new__(cls, name, bases, attrs):
        if name == "Model":
            return super().__new__(cls, name, bases, attrs)
            
        fields = {}
        m2m_fields = {}
        
        
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
        
        
        for field in new_class._fields.values():
            field.model = new_class
        for m2m_field in new_class._m2m_fields.values():
            m2m_field.model = new_class

        return new_class


class QuerySet:
    OPERATORS = {
        'exact': '=', 'gt': '>', 'lt': '<', 'gte': '>=', 'lte': '<=',
        'ne': '!=', 'in': 'IN', 'contains': 'LIKE',
        'startswith': 'LIKE', 'endswith': 'LIKE'
    }

    def __init__(self, model: Type["Model"]):
        self.model = model
        self.db = model._db
        self._filters: List[Tuple] = []
        self._excludes: List[Tuple] = []
        self._order_by_clauses: List[str] = []
        self._limit_clause: Optional[int] = None

    def _clone(self):
        qs = QuerySet(self.model)
        qs._filters = self._filters.copy()
        qs._excludes = self._excludes.copy()
        qs._order_by_clauses = self._order_by_clauses.copy()
        qs._limit_clause = self._limit_clause
        return qs

    def filter(self, **kwargs):
        qs = self._clone()
        for key, value in kwargs.items():
            qs._filters.append((key, value))
        return qs

    def exclude(self, **kwargs):
        qs = self._clone()
        for key, value in kwargs.items():
            qs._excludes.append((key, value))
        return qs

    def order_by(self, *field_names: str):
        qs = self._clone()
        for field_name in field_names:
            direction = "DESC" if field_name.startswith('-') else "ASC"
            field = field_name.lstrip('-')
            qs._order_by_clauses.append(f"{field} {direction}")
        return qs

    def limit(self, n: int):
        qs = self._clone()
        qs._limit_clause = n
        return qs

    def _build_conditions(self, conditions_list: List[Tuple], joiner: str = "AND", prefix: str = "") -> Tuple[str, Tuple]:
        conditions = []
        values = []
        for k, v in conditions_list:
            parts = k.split('__')
            field_name = parts[0]
            op = parts[1] if len(parts) > 1 else 'exact'

            if op == 'in':
                placeholders = ','.join('?' for _ in v)
                conditions.append(f"{field_name} IN ({placeholders})")
                values.extend(v)
            elif op == 'contains':
                conditions.append(f"{field_name} LIKE ?")
                values.append(f"%{v}%")
            elif op == 'startswith':
                conditions.append(f"{field_name} LIKE ?")
                values.append(f"{v}%")
            elif op == 'endswith':
                conditions.append(f"{field_name} LIKE ?")
                values.append(f"%{v}")
            else:
                sql_op = self.OPERATORS.get(op, '=')
                conditions.append(f"{field_name} {sql_op} ?")
                values.append(v)

        clause = f" {joiner} ".join(conditions)
        return (f"{prefix}({clause})" if clause else ""), tuple(values)


    def _build_query(self, select_what: str = "*"):
        
        where_clause, where_values = self._build_conditions(self._filters, "AND")
        exclude_clause, exclude_values = self._build_conditions(self._excludes, "AND", "NOT ")

        all_conditions = []
        if where_clause: all_conditions.append(where_clause)
        if exclude_clause: all_conditions.append(exclude_clause)

        final_where = " WHERE " + " AND ".join(all_conditions) if all_conditions else ""
        
        
        order_by = ""
        if self._order_by_clauses:
            order_by = " ORDER BY " + ", ".join(self._order_by_clauses)
            
        
        limit = ""
        if self._limit_clause is not None:
            limit = f" LIMIT {self._limit_clause}"

        query = f"SELECT {select_what} FROM {self.model._table_name}{final_where}{order_by}{limit}"
        return query, where_values + exclude_values

    async def _execute_query(self):
        query, values = self._build_query()
        async with self.db._lock:
            await self.db.cur.execute(query, values)
            rows = await self.db.cur.fetchall()
            return [self.model(**dict(r)) for r in rows]

    async def all(self):
        return await self._execute_query()
    
    def __aiter__(self):
        return self._async_generator()

    async def _async_generator(self):
        results = await self.all()
        for item in results:
            yield item

    async def get(self, **kwargs):
        qs = self.filter(**kwargs)
        rows = await qs.limit(2).all() 
        if not rows:
            raise self.model.DoesNotExist(f"{self.model.__name__} matching query does not exist")
        if len(rows) > 1:
            raise self.model.MultipleObjectsReturned(f"Multiple {self.model.__name__} objects returned for query")
        return rows[0]

    async def first(self):
        res = await self.order_by('id').limit(1).all()
        return res[0] if res else None

    async def last(self):
        res = await self.order_by('-id').limit(1).all()
        return res[0] if res else None

    async def count(self):
        query, values = self._build_query(select_what="COUNT(*)")
        async with self.db._lock:
            await self.db.cur.execute(query, values)
            result = await self.db.cur.fetchone()
            return result[0] if result else 0

    async def _aggregate(self, func: str, field_name: str):
        query, values = self._build_query(select_what=f"{func}({field_name})")
        async with self.db._lock:
            await self.db.cur.execute(query, values)
            result = await self.db.cur.fetchone()
            return result[0] if result and result[0] is not None else None

    async def sum(self, field_name: str): return await self._aggregate("SUM", field_name)
    async def avg(self, field_name: str): return await self._aggregate("AVG", field_name)
    async def min(self, field_name: str): return await self._aggregate("MIN", field_name)
    async def max(self, field_name: str): return await self._aggregate("MAX", field_name)
    
    async def update(self, **kwargs):
        """آپدیت دسته‌ای رکوردهای منطبق با کوئری."""
        if not kwargs: return 0
        set_clause = ", ".join(f"{k} = ?" for k in kwargs)
        
        query, values = self._build_query()
        
        
        
        where_clause = ""
        if "WHERE" in query:
            where_clause = query.split("WHERE", 1)[1].split("ORDER BY")[0].split("LIMIT")[0]

        update_query = f"UPDATE {self.model._table_name} SET {set_clause} WHERE {where_clause}"
        
        async with self.db._lock:
            await self.db.cur.execute(update_query, (*kwargs.values(), *values))
            if self.db.auto_commit: await self.db.conn.commit()
            return self.db.cur.rowcount
            
    async def delete(self):
        """حذف دسته‌ای رکوردهای منطبق با کوئری."""
        query, values = self._build_query()
        where_clause = ""
        if "WHERE" in query:
            where_clause = query.split("WHERE", 1)[1].split("ORDER BY")[0].split("LIMIT")[0].strip()

        if where_clause:
            delete_query = f"DELETE FROM {self.model._table_name} WHERE {where_clause}"
            params = values
        else:
            
            delete_query = f"DELETE FROM {self.model._table_name}"
            params = ()

        async with self.db._lock:
            await self.db.cur.execute(delete_query, params)
            if self.db.auto_commit:
                await self.db.conn.commit()
            return self.db.cur.rowcount



class Model(metaclass=ModelMeta):
    _db: "Connection" = None
    DoesNotExist = DoesNotExist
    MultipleObjectsReturned = MultipleObjectsReturned

    @classmethod
    def bind(cls, db: "Connection"):
        cls._db = db

    @classmethod
    async def create_table(cls):
        fields_defs = []
        foreign_keys = []
        for name, field in cls._fields.items():
            if isinstance(field, ForeignKey):
                
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

            
            if not isinstance(field, ForeignKey):
                fields_defs.append(s)

        query = f"CREATE TABLE IF NOT EXISTS {cls._table_name} ({', '.join(fields_defs + foreign_keys)})"
        await cls._db.cur.execute(query)
        
        
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
        
        for name, field in self._fields.items():
            if not isinstance(field, ForeignKey): 
                value = kwargs.get(name, field.default)
                setattr(self, name, field.to_python(value))
        
        
        for name, field in self._fields.items():
            if isinstance(field, ForeignKey):
                
                if name in kwargs:
                    setattr(self, name, kwargs[name])
                
                elif f"{name}_id" in kwargs:
                     setattr(self, f"{name}_id", kwargs[f"{name}_id"])

        
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
            
            keys = ", ".join(data.keys())
            placeholders = ", ".join("?" for _ in data)
            query = f"INSERT INTO {self._table_name} ({keys}) VALUES ({placeholders})"
            async with self._db._lock:
                await self._db.cur.execute(query, tuple(data.values()))
                self.id = self._db.cur.lastrowid 
                if self._db.auto_commit: await self._db.conn.commit()
        else:
            
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


class Connection:
    def __init__(self, filename="nivo_orm.db", auto_commit=True):
        self.filename = filename
        self.auto_commit = auto_commit
        self._lock = asyncio.Lock()
        self.conn = None
        self.cur = None
        self.models = []

    async def connect(self):
        self.conn = await aiosqlite.connect(self.filename)
        
        await self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.row_factory = aiosqlite.Row
        self.cur = await self.conn.cursor()

    async def close(self):
        if self.conn:
            await self.conn.close()

    @asynccontextmanager
    async def atomic(self):
        original_auto_commit = self.auto_commit
        self.auto_commit = False
        async with self._lock:
            await self.conn.execute("BEGIN")
            try:
                yield
                await self.conn.commit()
            except Exception as e:
                await self.conn.rollback()
                raise e
            finally:
                self.auto_commit = original_auto_commit