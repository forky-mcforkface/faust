import sys
from typing import Any, MutableMapping, Optional, Type, cast
from .codecs import CodecArg, CodecT, dumps, loads
from ..exceptions import KeyDecodeError, ValueDecodeError
from ..types import K, ModelArg, ModelT, V
from ..types.serializers import AsyncSerializerT, RegistryT
from ..utils.compat import want_bytes
from ..utils.imports import FactoryMapping, symbol_by_name
from ..utils.objects import cached_property

_flake8_Any_is_really_used: Any  # XXX flake8 bug

__all__ = ['Registry']


class Registry(RegistryT):

    #: Mapping of serializers that needs to be async
    override_classes: FactoryMapping[Type[AsyncSerializerT]] = FactoryMapping(
        avro='faust.serializers.avro.faust:AvroSerializer',
    )
    override_classes.include_setuptools_namespace('faust.async_serializers')

    #: Async serializer instances are cached here.
    _override: MutableMapping[CodecArg, AsyncSerializerT] = None

    def __init__(self,
                 key_serializer: CodecArg = None,
                 value_serializer: CodecArg = 'json') -> None:
        self.key_serializer = key_serializer
        self.value_serializer = value_serializer
        self._override = {}

    async def loads_key(self, typ: Optional[ModelArg], key: bytes) -> K:
        """Deserialize message key.

        Arguments:
            typ: Model to use for deserialization.
            key: Serialized key.
        """
        if key is None or typ is None:
            return key
        try:
            if typ is None or isinstance(typ, (str, CodecT)):
                k = self.Model._maybe_reconstruct(
                    await self._loads(self.key_serializer, key))
            else:
                k = await self._loads_model(
                    cast(Type[ModelT], typ), self.key_serializer, key)
            return cast(K, k)
        except Exception as exc:
            raise KeyDecodeError(
                str(exc)).with_traceback(sys.exc_info()[2]) from None

    async def _loads_model(
            self,
            typ: Type[ModelT],
            default_serializer: CodecArg,
            data: bytes) -> Any:
        data = await self._loads(
            typ._options.serializer or default_serializer, data)
        self_cls = self.Model._maybe_namespace(data)
        return self_cls(data) if self_cls else typ(data)

    async def _loads(self, serializer: CodecArg, data: bytes) -> Any:
        try:
            ser = self._get_serializer(serializer)
        except KeyError:
            return loads(serializer, data)
        else:
            return await ser.loads(data)

    async def loads_value(self, typ: ModelArg, value: bytes) -> Any:
        """Deserialize value.

        Arguments:
            typ: Model to use for deserialization.
            value: Bytestring to deserialize.
        """
        if value is None:
            return None
        try:
            serializer = self.value_serializer
            if typ is None or isinstance(typ, (str, CodecT)):
                return self.Model._maybe_reconstruct(
                    await self._loads(serializer, value))
            else:
                return await self._loads_model(
                    cast(Type[ModelT], typ), serializer, value)
        except Exception as exc:
            raise ValueDecodeError(
                str(exc)).with_traceback(sys.exc_info()[2]) from None

    async def dumps_key(self, topic: str, key: K,
                        serializer: CodecArg = None) -> Optional[bytes]:
        """Serialize key.

        Arguments:
            topic: The topic that the message will be sent to.
            key: The key to be serialized.
            serializer: Custom serializer to use if value is not a Model.
        """
        serializer = self.key_serializer
        is_model = False
        if isinstance(key, ModelT):
            is_model = True
            key = cast(ModelT, key)
            serializer = key._options.serializer or serializer

        if serializer:
            try:
                ser = self._get_serializer(serializer)
            except KeyError:
                if is_model:
                    return cast(ModelT, key).dumps(serializer=serializer)
                return dumps(serializer, key)
            else:
                return await ser.dumps_key(topic, cast(ModelT, key))

        return want_bytes(cast(bytes, key)) if key is not None else None

    async def dumps_value(self, topic: str, value: V,
                          serializer: CodecArg = None) -> Optional[bytes]:
        """Serialize value.

        Arguments:
            topic: The topic that the message will be sent to.
            value: The value to be serialized.
            serializer: Custom serializer to use if value is not a Model.
        """
        is_model = False
        if isinstance(value, ModelT):
            is_model = True
            value = cast(ModelT, value)
            serializer = value._options.serializer or self.value_serializer
        if serializer:
            try:
                ser = self._get_serializer(serializer)
            except KeyError:
                if is_model:
                    return cast(ModelT, value).dumps(serializer=serializer)
                return dumps(serializer, value)
            else:
                return await ser.dumps_value(topic, cast(ModelT, value))
        return cast(bytes, value)

    def _get_serializer(self, name: CodecArg) -> AsyncSerializerT:
        # Caches overridden AsyncSerializer
        # e.g. the avro serializer communicates with a Schema registry
        # server, so it needs to be async.
        # See Registry.dumps_key, .dumps_value, .loads_key, .loads_value,
        # and the AsyncSerializer implementation in
        #   faust/utils/avro/faust.py
        if not isinstance(name, str):
            raise KeyError(name)
        try:
            return self._override[name]
        except KeyError:
            ser = self._override[name] = symbol_by_name(
                self.override_classes.get_alias(name))(self)
            return cast(AsyncSerializerT, ser)

    @cached_property
    def Model(self) -> Type[ModelT]:
        from ..models.base import Model
        return Model
