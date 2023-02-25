__all__ = [
    "Pack",
    "PackType",
    "PackFile",
    "ExtraContainer",
    "SupportsExtra",
    "ExtraPin",
    "NamespaceExtraContainer",
    "PackExtraContainer",
    "Mcmeta",
    "McmetaPin",
    "PackPin",
    "Namespace",
    "NamespaceFile",
    "NamespaceContainer",
    "NamespacePin",
    "NamespaceProxy",
    "NamespaceProxyDescriptor",
    "MergeCallback",
    "MergePolicy",
    "UnveilMapping",
    "PackOverwrite",
    "PACK_COMPRESSION",
    "LATEST_MINECRAFT_VERSION",
]


import shutil
from collections import defaultdict
from contextlib import nullcontext
from dataclasses import dataclass, field
from functools import partial
from itertools import count
from pathlib import Path, PurePosixPath
from typing import (
    Any,
    Callable,
    ClassVar,
    Generic,
    Iterable,
    Iterator,
    Literal,
    Mapping,
    MutableMapping,
    Optional,
    Protocol,
    TypeVar,
    cast,
    get_origin,
    overload,
)
from zipfile import ZIP_BZIP2, ZIP_DEFLATED, ZIP_LZMA, ZIP_STORED, ZipFile

from typing_extensions import Self

from beet.core.container import (
    CV,
    Drop,
    MatchMixin,
    MergeableType,
    MergeContainer,
    MergeContainerProxy,
    Pin,
)
from beet.core.file import (
    File,
    FileOrigin,
    JsonFileBase,
    MutableFileOrigin,
    PngFileBase,
)
from beet.core.utils import (
    FileSystemPath,
    JsonDict,
    T,
    TextComponent,
    get_first_generic_param_type,
)

from .utils import list_extensions, list_files

LATEST_MINECRAFT_VERSION: str = "1.19"


PackFileType = TypeVar("PackFileType", bound="PackFile")
NamespaceType = TypeVar("NamespaceType", bound="Namespace")
NamespaceFileType = TypeVar("NamespaceFileType", bound="NamespaceFile")
PackType = TypeVar("PackType", bound="Pack[Any]")

PackFile = File[Any, Any]


PACK_COMPRESSION: dict[str, int] = {
    "none": ZIP_STORED,
    "deflate": ZIP_DEFLATED,
    "bzip2": ZIP_BZIP2,
    "lzma": ZIP_LZMA,
}


class NamespaceFile(Protocol):
    """Protocol for detecting files that belong in pack namespaces."""

    scope: ClassVar[tuple[str, ...]]
    extension: ClassVar[str]

    def __init__(
        self,
        _content: Optional[Any] = None,
        /,
        *,
        source_path: Optional[FileSystemPath] = None,
        source_start: Optional[int] = None,
        source_stop: Optional[int] = None,
        on_bind: Optional[Callable[[Any, Any, str], Any]] = None,
        original: Any = None,
    ) -> None:
        ...

    def merge(self, other: Any) -> bool:
        ...

    def bind(self, pack: Any, path: str) -> Any:
        ...

    def set_content(self, content: Any):
        ...

    def get_content(self) -> Any:
        ...

    def ensure_source_path(self) -> FileSystemPath:
        ...

    def ensure_serialized(
        self,
        serializer: Optional[Callable[[Any], Any]] = None,
    ) -> Any:
        ...

    def ensure_deserialized(
        self,
        deserializer: Optional[Callable[[Any], Any]] = None,
    ) -> Any:
        ...

    @classmethod
    def default(cls) -> Any:
        ...

    @classmethod
    def load(cls, origin: FileOrigin, path: FileSystemPath) -> Self:
        ...

    def dump(self, origin: MutableFileOrigin, path: FileSystemPath):
        ...


class MergeCallback(Protocol):
    """Protocol for detecting merge callbacks."""

    def __call__(
        self, pack: Any, path: str, current: MergeableType, conflict: MergeableType, /
    ) -> bool:
        ...


@dataclass
class MergePolicy:
    """Class holding lists of rules for merging files."""

    extra: dict[str, list[MergeCallback]] = field(default_factory=dict)
    namespace: dict[type[NamespaceFile], list[MergeCallback]] = field(
        default_factory=dict
    )
    namespace_extra: dict[str, list[MergeCallback]] = field(default_factory=dict)

    def extend(self, other: "MergePolicy"):
        for rules, other_rules in [
            (self.extra, other.extra),
            (self.namespace, other.namespace),
            (self.namespace_extra, other.namespace_extra),
        ]:
            for key, value in other_rules.items():
                rules.setdefault(key, []).extend(value)  # type: ignore

    def extend_extra(self, filename: str, rule: MergeCallback):
        """Add rule for merging extra files."""
        self.extra.setdefault(filename, []).append(rule)

    def extend_namespace(self, file_type: type[NamespaceFile], rule: MergeCallback):
        """Add rule for merging namespace files."""
        self.namespace.setdefault(file_type, []).append(rule)

    def extend_namespace_extra(self, filename: str, rule: MergeCallback):
        """Add rule for merging namespace extra files."""
        self.namespace_extra.setdefault(filename, []).append(rule)

    def merge_with_rules(
        self,
        pack: Any,
        current: MutableMapping[str, MergeableType],
        other: Mapping[str, MergeableType],
        map_rules: Callable[[str], tuple[str, list[MergeCallback]]],
    ) -> bool:
        """Merge values according to the given rules."""
        for key, value in other.items():
            if key not in current:
                current[key] = value
                continue

            current_value = current[key]
            path, rules = map_rules(key)

            try:
                for rule in rules:
                    if rule(pack, path, current_value, value):
                        break
                else:
                    if not current_value.merge(value):
                        current[key] = value
            except Drop:
                del current[key]

        return True


class ExtraContainer(MatchMixin, MergeContainer[str, PackFile]):
    """Container that stores extra files in a pack or a namespace."""


class SupportsExtra(Protocol):
    """Protocol for detecting extra container."""

    extra: ExtraContainer


ExtraPinType = TypeVar("ExtraPinType", bound=Optional[PackFile], covariant=True)


class ExtraPin(Pin[str, ExtraPinType]):
    """Descriptor that makes a specific file accessible through attribute lookup."""

    def forward(self, obj: SupportsExtra) -> ExtraContainer:
        return obj.extra


class NamespaceExtraContainer(ExtraContainer, Generic[NamespaceType]):
    """Namespace extra container."""

    namespace: Optional[NamespaceType] = None

    def process(self, key: str, value: PackFile) -> PackFile:
        if (
            self.namespace is not None
            and self.namespace.pack is not None
            and self.namespace.name
        ):
            value.bind(self.namespace.pack, f"{self.namespace.name}:{key}")
        return value

    def bind(self, namespace: NamespaceType):
        """Handle insertion."""
        self.namespace = namespace

        for key, value in self.items():
            try:
                self.process(key, value)
            except Drop:
                del self[key]

    def merge(self, other: Mapping[str, PackFile]) -> bool:
        if (
            self.namespace is not None
            and self.namespace.pack is not None
            and self.namespace.name
        ):
            pack = self.namespace.pack
            name = self.namespace.name

            return pack.merge_policy.merge_with_rules(
                pack=pack,
                current=self,
                other=other,
                map_rules=lambda key: (
                    f"{name}:{key}",
                    pack.merge_policy.namespace_extra.get(key, []),
                ),
            )

        return super().merge(other)


class PackExtraContainer(ExtraContainer, Generic[PackType]):
    """Pack extra container."""

    pack: Optional[PackType] = None

    def process(self, key: str, value: PackFile) -> PackFile:
        if self.pack is not None:
            value.bind(self.pack, key)
        return value

    def bind(self, pack: PackType):
        """Handle insertion."""
        self.pack = pack

        for key, value in self.items():
            try:
                self.process(key, value)
            except Drop:
                del self[key]

    def merge(self, other: Mapping[str, PackFile]) -> bool:
        if self.pack is not None:
            pack = self.pack

            return pack.merge_policy.merge_with_rules(
                pack=pack,
                current=self,
                other=other,
                map_rules=lambda key: (
                    key,
                    pack.merge_policy.extra.get(key, []),
                ),
            )
        return super().merge(other)


class NamespaceContainer(MatchMixin, MergeContainer[str, NamespaceFileType]):
    """Container that stores one type of files in a namespace."""

    namespace: Optional["Namespace"] = None
    file_type: Optional[type[NamespaceFileType]] = None

    def process(self, key: str, value: NamespaceFileType) -> NamespaceFileType:
        if (
            self.namespace is not None
            and self.namespace.pack is not None
            and self.namespace.name
        ):
            value.bind(self.namespace.pack, f"{self.namespace.name}:{key}")

        return value

    def bind(self, namespace: "Namespace", file_type: type[NamespaceFileType]):
        """Handle insertion."""
        self.namespace = namespace
        self.file_type = file_type

        for key, value in self.items():
            try:
                self.process(key, value)
            except Drop:
                del self[key]

    def setdefault(
        self,
        key: str,
        default: Optional[NamespaceFileType] = None,
    ) -> NamespaceFileType:
        if value := self.get(key):
            return value

        if default:
            self[key] = default
        else:
            if not self.file_type:
                raise ValueError(
                    "File type associated to the namespace container is not available."
                )
            self[key] = self.file_type()

        return self[key]

    def merge(self, other: Mapping[str, NamespaceFileType]) -> bool:
        if (
            self.namespace is not None
            and self.namespace.pack is not None
            and self.namespace.name
            and self.file_type is not None
        ):
            pack = self.namespace.pack
            name = self.namespace.name
            file_type = self.file_type

            return pack.merge_policy.merge_with_rules(
                pack=pack,
                current=self,
                other=other,
                map_rules=lambda key: (
                    f"{name}:{key}",
                    pack.merge_policy.namespace.get(file_type, []),
                ),
            )
        return super().merge(other)

    def generate_tree(self, path: str = "") -> dict[Any, Any]:
        """Generate a hierarchy of nested dictionaries representing the files and folders."""
        prefix = path.split("/") if path else []
        tree: dict[Any, Any] = {}

        for filename, file_instance in self.items():
            parts = filename.split("/")

            if parts[: len(prefix)] != prefix:
                continue

            parent = tree
            for part in parts[len(prefix) :]:
                parent = parent.setdefault(part, {})

            parent[self.file_type] = file_instance

        return tree


class NamespacePin(Pin[type[NamespaceFileType], NamespaceContainer[NamespaceFileType]]):
    """Descriptor for accessing namespace containers by attribute lookup."""


class Namespace(MergeContainer[type[NamespaceFile], NamespaceContainer[NamespaceFile]]):
    """Class representing a namespace."""

    pack: Optional["Pack[Self]"] = None
    name: Optional[str] = None
    extra: NamespaceExtraContainer["Namespace"]

    directory: ClassVar[str]
    field_map: ClassVar[Mapping[type[NamespaceFile], str]]
    scope_map: ClassVar[Mapping[tuple[tuple[str, ...], str], type[NamespaceFile]]]

    def __init_subclass__(cls):
        pins = NamespacePin[NamespaceFileType].collect_from(cls)
        cls.field_map = {pin.key: attr for attr, pin in pins.items()}
        cls.scope_map = {
            (pin.key.scope, pin.key.extension): pin.key for pin in pins.values()
        }

    def __init__(self):
        super().__init__()
        self.extra = NamespaceExtraContainer()

    def process(
        self,
        key: type[NamespaceFile],
        value: NamespaceContainer[NamespaceFile],
    ) -> NamespaceContainer[NamespaceFile]:
        value.bind(self, key)
        return value

    def bind(self, pack: "Pack[Self]", name: str):
        """Handle insertion."""
        self.pack = pack
        self.name = name

        for key, value in self.items():
            self.process(key, value)

        self.extra.bind(self)

    @overload
    def __setitem__(
        self,
        key: type[NamespaceFile],
        value: NamespaceContainer[NamespaceFile],
    ):
        ...

    @overload
    def __setitem__(self, key: str, value: NamespaceFile):
        ...

    def __setitem__(
        self,
        key: type[NamespaceFile] | str,
        value: NamespaceContainer[NamespaceFile] | NamespaceFile,
    ):
        if isinstance(key, type):
            value = cast(NamespaceContainer[NamespaceFile], value)
            super().__setitem__(key, value)
        else:
            value = cast(NamespaceFile, value)
            self[type(value)][key] = value

    def __eq__(self, other: Any) -> bool:
        if self is other:
            return True

        if type(self) == type(other) and not self.extra == other.extra:
            return False

        if isinstance(other, Mapping):
            rhs: Mapping[type[NamespaceFile], NamespaceContainer[NamespaceFile]] = other
            return all(self[key] == rhs[key] for key in self.keys() | rhs.keys())

        return NotImplemented

    def __bool__(self) -> bool:
        return any(self.values()) or bool(self.extra)

    def missing(self, key: type[NamespaceFile]) -> NamespaceContainer[NamespaceFile]:
        return NamespaceContainer()

    def merge(
        self, other: Mapping[type[NamespaceFile], NamespaceContainer[NamespaceFile]]
    ) -> bool:
        super().merge(other)

        if isinstance(self, Namespace) and isinstance(other, Namespace):
            self.extra.merge(other.extra)

        empty_containers = [key for key, value in self.items() if not value]
        for container in empty_containers:
            del self[container]

        return True

    def clear(self):
        self.extra.clear()
        super().clear()

    @property
    def content(self) -> Iterator[tuple[str, NamespaceFile]]:
        """Iterator that yields all the files stored in the namespace."""
        for container in self.values():
            yield from container.items()

    @overload
    def list_files(
        self,
        namespace: str,
        *extensions: str,
    ) -> Iterator[tuple[str, PackFile]]:
        ...

    @overload
    def list_files(
        self,
        namespace: str,
        *extensions: str,
        extend: type[T],
    ) -> Iterator[tuple[str, T]]:
        ...

    def list_files(
        self,
        namespace: str,
        *extensions: str,
        extend: Optional[Any] = None,
    ) -> Iterator[tuple[str, Any]]:
        """List and filter all the files in the namespace."""
        if extend and (origin := get_origin(extend)):
            extend = origin

        for path, item in self.extra.items():
            if extensions and not any(path.endswith(ext) for ext in extensions):
                continue
            if extend and not isinstance(item, extend):
                continue
            yield f"{self.directory}/{namespace}/{path}", item

        for content_type, container in self.items():
            if not container:
                continue
            if extensions and content_type.extension not in extensions:
                continue
            if extend and not issubclass(content_type, extend):
                continue
            prefix = "/".join((self.directory, namespace) + content_type.scope)
            for name, item in container.items():
                yield f"{prefix}/{name}{content_type.extension}", item

    @classmethod
    def get_extra_info(cls) -> dict[str, type[PackFile]]:
        return {}

    @classmethod
    def scan(
        cls,
        prefix: str,
        origin: FileOrigin,
        extend_namespace: Iterable[type[NamespaceFile]] = (),
        extend_namespace_extra: Optional[Mapping[str, type[PackFile]]] = None,
    ) -> Iterator[tuple[str, "Namespace"]]:
        """Load namespaces by walking through a zipfile or directory."""
        preparts = tuple(filter(None, prefix.split("/")))
        if preparts and preparts[0] != cls.directory:
            return

        if isinstance(origin, ZipFile):
            filenames = map(PurePosixPath, origin.namelist())
        elif isinstance(origin, Mapping):
            filenames = map(PurePosixPath, origin)
        elif Path(origin).is_file():
            filenames = [PurePosixPath()]
        else:
            filenames = list_files(origin)

        extra_info = cls.get_extra_info()
        if extend_namespace_extra:
            extra_info.update(extend_namespace_extra)

        scope_map = dict(cls.scope_map)
        for file_type in extend_namespace:
            scope_map[file_type.scope, file_type.extension] = file_type

        name = None
        namespace = None

        for filename in sorted(filenames):
            try:
                directory, namespace_dir, *scope, basename = preparts + filename.parts
            except ValueError:
                continue

            if directory != cls.directory:
                continue
            if name != namespace_dir:
                if name and namespace:
                    yield name, namespace
                name, namespace = namespace_dir, cls()

            assert name and namespace is not None
            extensions = list_extensions(PurePosixPath(basename))

            if file_type := extra_info.get(path := "/".join(scope + [basename])):
                namespace.extra[path] = file_type.load(origin, filename)
                continue

            file_dir: list[str] = []

            while path := tuple(scope):
                for extension in extensions:
                    if file_type := scope_map.get((path, extension)):
                        key = "/".join(file_dir + [basename[: -len(extension)]])
                        namespace[file_type][key] = file_type.load(origin, filename)
                        break
                else:
                    file_dir.insert(0, scope.pop())
                    continue
                break

        if name and namespace:
            yield name, namespace

    def dump(self, namespace: str, origin: MutableFileOrigin):
        """Write the namespace to a zipfile or to the filesystem."""
        _dump_files(origin, dict(self.list_files(namespace)))

    def __repr__(self) -> str:
        args = ", ".join(
            f"{self.field_map[key]}={value}"
            for key, value in self.items()
            if key in self.field_map and value
        )
        return f"{self.__class__.__name__}({args})"


class NamespaceProxy(
    MatchMixin,
    MergeContainerProxy[type[NamespaceFileType], str, NamespaceFileType],
):
    """Aggregated view that exposes a certain type of files over all namespaces."""

    def split_key(self, key: str) -> tuple[str, str]:
        namespace, _, file_path = key.partition(":")
        if not file_path:
            raise KeyError(key)
        return namespace, file_path

    def join_key(self, key1: str, key2: str) -> str:
        return f"{key1}:{key2}"

    def setdefault(
        self,
        key: str,
        default: Optional[NamespaceFileType] = None,
    ) -> NamespaceFileType:
        key1, key2 = self.split_key(key)
        return self.proxy[key1][self.proxy_key].setdefault(key2, default)  # type: ignore

    def merge(self, other: Mapping[str, NamespaceFileType]) -> bool:
        if isinstance(pack := self.proxy, Pack):
            return pack.merge_policy.merge_with_rules(
                pack=pack,
                current=self,
                other=other,
                map_rules=lambda key: (
                    key,
                    pack.merge_policy.namespace.get(self.proxy_key, []),
                ),
            )
        return super().merge(other)

    def walk(self) -> Iterator[tuple[str, set[str], dict[str, NamespaceFileType]]]:
        """Walk over the file hierarchy."""
        for prefix, namespace in self.proxy.items():
            separator = ":"
            roots: list[tuple[str, dict[Any, Any]]] = [
                (prefix, namespace[self.proxy_key].generate_tree())  # type: ignore
            ]

            while roots:
                prefix, root = roots.pop()

                dirs: set[str] = set()
                files: dict[str, NamespaceFileType] = {}

                for key, value in root.items():
                    if not isinstance(key, str):
                        continue
                    if any(isinstance(name, str) for name in value):
                        dirs.add(key)
                    if file_instance := value.get(self.proxy_key, None):
                        files[key] = file_instance

                yield prefix + separator, dirs, files

                for directory in dirs:
                    roots.append((prefix + separator + directory, root[directory]))

                separator = "/"


@dataclass
class NamespaceProxyDescriptor(Generic[NamespaceFileType]):
    """Descriptor that dynamically instantiates a namespace proxy."""

    proxy_key: type[NamespaceFileType]

    def __get__(
        self, obj: Any, objtype: Optional[type[Any]] = None
    ) -> NamespaceProxy[NamespaceFileType]:
        return NamespaceProxy[NamespaceFileType](obj, self.proxy_key)


class Mcmeta(JsonFileBase[JsonDict]):
    """Class representing a pack.mcmeta file."""

    def merge(self, other: Self) -> bool:
        for key, value in other.data.items():
            if key == "filter":
                block = self.data.setdefault("filter", {}).setdefault("block", [])
                for item in value.get("block", []):
                    if item not in block:
                        block.append(item)
            else:
                self.data[key] = value
        return True

    @classmethod
    def default(cls) -> JsonDict:
        return {}


class McmetaPin(Pin[str, CV]):
    """Descriptor that makes it possible to bind pack.mcmeta information to attribute lookup."""

    def forward(self, obj: "Pack[Namespace]") -> JsonDict:
        return obj.mcmeta.data


class PackPin(McmetaPin[CV]):
    """Descriptor that makes pack metadata accessible through attribute lookup."""

    def forward(self, obj: "Pack[Namespace]") -> JsonDict:
        return super().forward(obj).setdefault("pack", {})


class UnveilMapping(Mapping[str, FileSystemPath]):
    """Unveil mapping."""

    files: Mapping[str, FileSystemPath]
    prefix: str

    def __init__(self, files: Mapping[str, FileSystemPath], prefix: str = ""):
        self.files = files
        self.prefix = prefix

    def with_prefix(self, prefix: str) -> "UnveilMapping":
        return self.__class__(self.files, prefix)

    def __getitem__(self, key: str) -> FileSystemPath:
        sep = "/" if key and self.prefix else ""
        return self.files[f"{self.prefix}{sep}{key}"]

    def __iter__(self) -> Iterator[str]:
        if self.prefix:
            directory_prefix = f"{self.prefix}/"
            for key in self.files:
                if key == self.prefix:
                    yield ""
                elif key.startswith(directory_prefix):
                    yield key[len(directory_prefix) :]
        else:
            yield from self.files

    def __len__(self) -> int:
        return len(self.files)

    def __eq__(self, other: Any) -> bool:
        return self is other

    def __hash__(self) -> int:
        return id(self)

    def __repr__(self) -> str:
        args = f"files={self.files}"
        if self.prefix:
            args += f"prefix={self.prefix!r}"
        return f"{self.__class__.__name__}({args})"


class PackOverwrite(Exception):
    """Raised when trying to overwrite a pack."""

    path: FileSystemPath

    def __init__(self, path: FileSystemPath) -> None:
        super().__init__(path)
        self.path = path

    def __str__(self) -> str:
        return f'Couldn\'t overwrite "{str(self.path)}".'


class Pack(MatchMixin, MergeContainer[str, NamespaceType], Generic[NamespaceType]):
    """Class representing a pack."""

    name: Optional[str]
    path: Optional[Path]
    zipped: bool
    compression: Optional[Literal["none", "deflate", "bzip2", "lzma"]]
    compression_level: Optional[int]

    extra: PackExtraContainer[Self]
    mcmeta: ExtraPin[Mcmeta] = ExtraPin("pack.mcmeta", default_factory=lambda: Mcmeta())

    icon: ExtraPin[Optional[PngFileBase]] = ExtraPin("pack.png", default=None)

    description: PackPin[TextComponent] = PackPin("description", default="")
    pack_format: PackPin[int] = PackPin("pack_format", default=0)
    filter: McmetaPin[JsonDict] = McmetaPin(
        "filter", default_factory=lambda: {"block": []}
    )

    extend_extra: dict[str, type[PackFile]]
    extend_namespace: list[type[NamespaceFile]]
    extend_namespace_extra: dict[str, type[PackFile]]

    merge_policy: MergePolicy
    unveiled: dict[Path | UnveilMapping, set[str]]

    namespace_type: ClassVar[type[Namespace]]
    default_name: ClassVar[str]
    pack_format_registry: ClassVar[dict[tuple[int, ...], int]]
    latest_pack_format: ClassVar[int]

    def __init_subclass__(cls):
        if (namespace_type := get_first_generic_param_type(cls)) and issubclass(
            namespace_type, Namespace
        ):
            cls.namespace_type = namespace_type
        else:
            raise TypeError(
                "The namespace type for pack subclasses should be the first generic"
            )

    def __init__(
        self,
        name: Optional[str] = None,
        path: Optional[FileSystemPath] = None,
        zipfile: Optional[ZipFile] = None,
        mapping: Optional[Mapping[str, FileSystemPath]] = None,
        zipped: bool = False,
        compression: Optional[Literal["none", "deflate", "bzip2", "lzma"]] = None,
        compression_level: Optional[int] = None,
        mcmeta: Optional[Mcmeta] = None,
        icon: Optional[PngFileBase] = None,
        description: Optional[str] = None,
        pack_format: Optional[int] = None,
        filter: Optional[JsonDict] = None,
        extend_extra: Optional[Mapping[str, type[PackFile]]] = None,
        extend_namespace: Iterable[type[NamespaceFile]] = (),
        extend_namespace_extra: Optional[Mapping[str, type[PackFile]]] = None,
        merge_policy: Optional[MergePolicy] = None,
    ):
        super().__init__()
        self.name = name
        self.path = None
        self.zipped = zipped
        self.compression = compression
        self.compression_level = compression_level

        self.extra = PackExtraContainer()
        self.extra.bind(self)

        if mcmeta is not None:
            self.mcmeta = mcmeta
        if icon is not None:
            self.icon = icon
        if description is not None:
            self.description = description
        if pack_format is not None:
            self.pack_format = pack_format
        if filter is not None:
            self.filter = filter

        self.extend_extra = dict(extend_extra or {})
        self.extend_namespace = list(extend_namespace)
        self.extend_namespace_extra = dict(extend_namespace_extra or {})

        self.merge_policy = MergePolicy()
        if merge_policy:
            self.merge_policy.extend(merge_policy)

        self.unveiled = {}

        self.load(path or zipfile or mapping)

    def configure(
        self: PackType,
        other: Optional[PackType] = None,
        *,
        extend_extra: Optional[Mapping[str, type[PackFile]]] = None,
        extend_namespace: Iterable[type[NamespaceFile]] = (),
        extend_namespace_extra: Optional[Mapping[str, type[PackFile]]] = None,
        merge_policy: Optional[MergePolicy] = None,
    ) -> PackType:
        """Helper for updating or copying configuration from another pack."""
        if other:
            self.extend_extra.update(other.extend_extra or {})
            self.extend_namespace.extend(other.extend_namespace)
            self.extend_namespace_extra.update(other.extend_namespace_extra or {})
            self.merge_policy.extend(other.merge_policy)

        self.extend_extra.update(extend_extra or {})
        self.extend_namespace.extend(extend_namespace)
        self.extend_namespace_extra.update(extend_namespace_extra or {})

        if merge_policy:
            self.merge_policy.extend(merge_policy)

        return self

    @overload
    def __getitem__(self, key: str) -> NamespaceType:
        ...

    @overload
    def __getitem__(
        self, key: type[NamespaceFileType]
    ) -> NamespaceProxy[NamespaceFileType]:
        ...

    def __getitem__(
        self, key: str | type[NamespaceFileType]
    ) -> NamespaceType | NamespaceProxy[NamespaceFileType]:
        if isinstance(key, str):
            return super().__getitem__(key)

        # Using [Any] to silence the variance mismatch
        return NamespaceProxy[Any](self, key)

    @overload
    def __setitem__(self, key: str, value: NamespaceType):
        ...

    @overload
    def __setitem__(self, key: str, value: NamespaceFile):
        ...

    def __setitem__(self, key: str, value: NamespaceType | NamespaceFile):
        if isinstance(value, Namespace):
            super().__setitem__(key, value)
        else:
            NamespaceProxy[NamespaceFile](self, type(value))[key] = value

    def __eq__(self, other: Any) -> bool:
        if self is other:
            return True

        if type(self) == type(other) and not (
            self.name == other.name and self.extra == other.extra
        ):
            return False

        if isinstance(other, Mapping):
            rhs: Mapping[str, Namespace] = other
            return all(self[key] == rhs[key] for key in self.keys() | rhs.keys())

        return NotImplemented

    def __hash__(self) -> int:
        return id(self)

    def __bool__(self) -> bool:
        return any(self.values()) or self.extra.keys() > {"pack.mcmeta"}

    def __enter__(self: T) -> T:
        return self

    def __exit__(self, *_):
        self.save(overwrite=True)

    def process(self, key: str, value: NamespaceType) -> NamespaceType:
        value.bind(self, key)
        return value

    def missing(self, key: str) -> NamespaceType:
        return self.namespace_type()  # type: ignore

    def merge(self, other: Mapping[str, NamespaceType]) -> bool:
        super().merge(other)

        if isinstance(other, Pack):
            self.extra.merge(other.extra)

        empty_namespaces = [key for key, value in self.items() if not value]
        for namespace in empty_namespaces:
            del self[namespace]

        return True

    @property
    def content(self) -> Iterator[tuple[str, NamespaceFile]]:
        """Iterator that yields all the files stored in the pack."""
        for file_type in self.resolve_scope_map().values():
            yield from NamespaceProxy[NamespaceFile](self, file_type).items()

    def clear(self):
        self.extra.clear()
        super().clear()
        if not self.pack_format:
            self.pack_format = self.latest_pack_format
        if not self.description:
            self.description = ""

    @overload
    def list_files(
        self,
        *extensions: str,
    ) -> Iterator[tuple[str, PackFile]]:
        ...

    @overload
    def list_files(
        self,
        *extensions: str,
        extend: type[T],
    ) -> Iterator[tuple[str, T]]:
        ...

    def list_files(
        self,
        *extensions: str,
        extend: Optional[Any] = None,
    ) -> Iterator[tuple[str, Any]]:
        """List and filter all the files in the pack."""
        if extend and (origin := get_origin(extend)):
            extend = origin

        for path, item in self.extra.items():
            if extensions and not any(path.endswith(ext) for ext in extensions):
                continue
            if extend and not isinstance(item, extend):
                continue
            yield path, item

        for namespace_name, namespace in self.items():
            yield from namespace.list_files(namespace_name, *extensions, extend=extend)  # type: ignore

    @classmethod
    def get_extra_info(cls) -> dict[str, type[PackFile]]:
        return {"pack.mcmeta": Mcmeta, "pack.png": PngFileBase}

    def resolve_extra_info(self) -> dict[str, type[PackFile]]:
        extra_info = self.get_extra_info()
        if self.extend_extra:
            extra_info.update(self.extend_extra)
        return extra_info

    def resolve_scope_map(
        self,
    ) -> dict[tuple[tuple[str, ...], str], type[NamespaceFile]]:
        scope_map = dict(self.namespace_type.scope_map)
        for file_type in self.extend_namespace:
            scope_map[file_type.scope, file_type.extension] = file_type
        return scope_map

    def resolve_namespace_extra_info(self) -> dict[str, type[PackFile]]:
        namespace_extra_info = self.namespace_type.get_extra_info()
        if self.extend_namespace_extra:
            namespace_extra_info.update(self.extend_namespace_extra)
        return namespace_extra_info

    def load(
        self,
        origin: Optional[FileOrigin] = None,
        extend_extra: Optional[Mapping[str, type[PackFile]]] = None,
        extend_namespace: Iterable[type[NamespaceFile]] = (),
        extend_namespace_extra: Optional[Mapping[str, type[PackFile]]] = None,
        merge_policy: Optional[MergePolicy] = None,
    ):
        """Load pack from a zipfile or from the filesystem."""
        self.extend_extra.update(extend_extra or {})
        self.extend_namespace.extend(extend_namespace)
        self.extend_namespace_extra.update(extend_namespace_extra or {})

        if merge_policy:
            self.merge_policy.extend(merge_policy)

        if origin and not isinstance(origin, Mapping):
            if not isinstance(origin, ZipFile):
                origin = Path(origin).resolve()
                self.path = origin.parent
                if origin.is_file():
                    origin = ZipFile(origin)
                elif not origin.is_dir():
                    self.name = origin.name
                    self.zipped = origin.suffix == ".zip"
                    origin = None
            if isinstance(origin, ZipFile):
                self.zipped = True
                self.name = origin.filename and Path(origin.filename).name
            elif origin:
                self.zipped = False
                self.name = origin.name
            if self.name and self.name.endswith(".zip"):
                self.name = self.name[:-4]

        if origin:
            self.mount("", origin)

        if not self.pack_format:
            self.pack_format = self.latest_pack_format
        if not self.description:
            self.description = ""

    def mount(self, prefix: str, origin: FileOrigin):
        """Mount files from a zipfile or from the filesystem."""
        files: dict[str, PackFile] = {}

        for filename, file_type in self.resolve_extra_info().items():
            if not prefix:
                if loaded := file_type.try_load(origin, filename):
                    files[filename] = loaded
            elif prefix == filename:
                if loaded := file_type.try_load(origin, ""):
                    files[filename] = loaded
            elif filename.startswith(prefix + "/"):
                if loaded := file_type.try_load(origin, filename[len(prefix) + 1 :]):
                    files[filename] = loaded

        self.extra.merge(files)

        namespaces = {
            name: namespace
            for name, namespace in self.namespace_type.scan(
                prefix,
                origin,
                self.extend_namespace,
                self.extend_namespace_extra,
            )
        }

        self.merge(namespaces)  # type: ignore

    def unveil(self, prefix: str, origin: FileSystemPath | UnveilMapping):
        """Lazily mount resources from the root of a pack on the filesystem."""
        if not isinstance(origin, UnveilMapping):
            origin = Path(origin).resolve()

        mounted = self.unveiled.setdefault(origin, set())

        if prefix in mounted:
            return

        to_remove: set[str] = set()
        for mnt in mounted:
            if prefix.startswith(mnt):
                return
            if mnt.startswith(prefix):
                to_remove.add(mnt)

        mounted -= to_remove
        mounted.add(prefix)

        if isinstance(origin, UnveilMapping):
            self.mount(prefix, origin.with_prefix(prefix))
        else:
            self.mount(prefix, origin / prefix)

    def dump(self, origin: MutableFileOrigin):
        """Write the content of the pack to a zipfile or to the filesystem"""
        extra = {path: item for path, item in self.extra.items()}
        _dump_files(origin, extra)

        for namespace_name, namespace in self.items():
            namespace.dump(namespace_name, origin)

    def save(
        self,
        directory: Optional[FileSystemPath] = None,
        path: Optional[FileSystemPath] = None,
        zipped: Optional[bool] = None,
        compression: Optional[Literal["none", "deflate", "bzip2", "lzma"]] = None,
        compression_level: Optional[int] = None,
        overwrite: Optional[bool] = False,
    ) -> Path:
        """Save the pack at the specified location."""
        if path:
            path = Path(path).resolve()
            self.zipped = path.suffix == ".zip"
            self.name = path.name[:-4] if self.zipped else path.name
            self.path = path.parent

        if zipped is not None:
            self.zipped = zipped
        if compression is not None:
            self.compression = compression
        if compression_level is not None:
            self.compression_level = compression_level

        suffix = ".zip" if self.zipped else ""
        factory: Any = (
            partial(
                ZipFile,
                mode="w",
                compression=PACK_COMPRESSION[self.compression or "deflate"],
                compresslevel=self.compression_level,
            )
            if self.zipped
            else nullcontext
        )

        if not directory:
            directory = self.path or Path.cwd()

        self.path = Path(directory).resolve()

        if not self.name:
            for i in count():
                self.name = self.default_name + (str(i) if i else "")
                if not (self.path / f"{self.name}{suffix}").exists():
                    break

        output_path = self.path / f"{self.name}{suffix}"

        if output_path.exists():
            if not overwrite:
                raise PackOverwrite(output_path)
            if output_path.is_dir():
                shutil.rmtree(output_path)
            else:
                output_path.unlink()

        if self.zipped:
            self.path.mkdir(parents=True, exist_ok=True)
        else:
            output_path.mkdir(parents=True, exist_ok=True)

        with factory(output_path) as pack:
            self.dump(pack)

        return output_path

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(name={self.name!r}, "
            f"description={self.description!r}, pack_format={self.pack_format!r})"
        )


def _dump_files(origin: MutableFileOrigin, files: Mapping[str, PackFile]):
    dirs: defaultdict[tuple[str, ...], list[tuple[str, PackFile]]] = defaultdict(list)

    for full_path, item in files.items():
        directory, _, filename = full_path.rpartition("/")
        dirs[(directory,) if directory else ()].append((filename, item))

    for directory, entries in dirs.items():
        if not isinstance(origin, ZipFile):
            Path(origin, *directory).resolve().mkdir(parents=True, exist_ok=True)

        for filename, f in entries:
            f.dump(origin, "/".join(directory + (filename,)))
