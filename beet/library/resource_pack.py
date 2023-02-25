__all__ = [
    "Atlas",
    "ResourcePack",
    "ResourcePackNamespace",
    "Blockstate",
    "Model",
    "Language",
    "Font",
    "GlyphSizes",
    "TrueTypeFont",
    "ShaderPost",
    "Shader",
    "FragmentShader",
    "VertexShader",
    "GlslShader",
    "Text",
    "TextureMcmeta",
    "Texture",
    "Sound",
    "SoundConfig",
    "Particle",
]


from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass
from typing import ClassVar, Optional

from beet.core.file import JsonFileBase, PngFileBase, RawBinaryFileBase, RawTextFileBase
from beet.core.utils import JsonDict, extra_field, split_version

from .base import (
    LATEST_MINECRAFT_VERSION,
    ExtraPin,
    McmetaPin,
    Namespace,
    NamespacePin,
    NamespaceProxyDescriptor,
    Pack,
    PackFile,
)


class Blockstate(JsonFileBase[JsonDict]):
    """Class representing a blockstate."""

    scope: ClassVar[tuple[str, ...]] = ("blockstates",)
    extension: ClassVar[str] = ".json"


class Model(JsonFileBase[JsonDict]):
    """Class representing a model."""

    scope: ClassVar[tuple[str, ...]] = ("models",)
    extension: ClassVar[str] = ".json"

    def merge(self, other: "Model") -> bool:
        overrides = self.data.get("overrides", [])
        merged_overrides = deepcopy(overrides)

        for other_override in other.data.get("overrides", []):
            other_predicate = other_override.get("predicate")

            for i, override in enumerate(overrides):
                if override.get("predicate") == other_predicate:
                    merged_overrides[i]["model"] = other_override["model"]
                    break
            else:
                merged_overrides.append(other_override)

        self.data = dict(other.data)
        if merged_overrides:
            self.data["overrides"] = merged_overrides

        return True


class Language(JsonFileBase[JsonDict]):
    """Class representing a language file."""

    scope: ClassVar[tuple[str, ...]] = ("lang",)
    extension: ClassVar[str] = ".json"

    def merge(self, other: "Language") -> bool:  # type: ignore
        self.data.update(other.data)
        return True

    @classmethod
    def default(cls) -> JsonDict:
        return {}


class Font(JsonFileBase[JsonDict]):
    """Class representing a font configuration file."""

    scope: ClassVar[tuple[str, ...]] = ("font",)
    extension: ClassVar[str] = ".json"

    def merge(self, other: "Font") -> bool:  # type: ignore
        providers = self.data.setdefault("providers", [])

        for provider in other.data.get("providers", []):
            providers.append(deepcopy(provider))
        return True


class GlyphSizes(RawBinaryFileBase):
    """Class representing a legacy unicode glyph size file."""

    scope: ClassVar[tuple[str, ...]] = ("font",)
    extension: ClassVar[str] = ".bin"


class TrueTypeFont(RawBinaryFileBase):
    """Class representing a TrueType font."""

    scope: ClassVar[tuple[str, ...]] = ("font",)
    extension: ClassVar[str] = ".ttf"


class ShaderPost(JsonFileBase[JsonDict]):
    """Class representing a shader post-processing pipeline."""

    scope: ClassVar[tuple[str, ...]] = ("shaders", "post")
    extension: ClassVar[str] = ".json"


class Shader(JsonFileBase[JsonDict]):
    """Class representing a shader."""

    scope: ClassVar[tuple[str, ...]] = ("shaders",)
    extension: ClassVar[str] = ".json"


class FragmentShader(RawTextFileBase):
    """Class representing a fragment shader."""

    scope: ClassVar[tuple[str, ...]] = ("shaders",)
    extension: ClassVar[str] = ".fsh"


class VertexShader(RawTextFileBase):
    """Class representing a vertex shader."""

    scope: ClassVar[tuple[str, ...]] = ("shaders",)
    extension: ClassVar[str] = ".vsh"


class GlslShader(RawTextFileBase):
    """Class representing a glsl shader."""

    scope: ClassVar[tuple[str, ...]] = ("shaders",)
    extension: ClassVar[str] = ".glsl"


class Text(RawTextFileBase):
    """Class representing a text file."""

    scope: ClassVar[tuple[str, ...]] = ("texts",)
    extension: ClassVar[str] = ".txt"


class TextureMcmeta(JsonFileBase[JsonDict]):
    """Class representing a texture mcmeta."""

    scope: ClassVar[tuple[str, ...]] = ("textures",)
    extension: ClassVar[str] = ".png.mcmeta"


@dataclass(eq=False, repr=False)
class Texture(PngFileBase):
    """Class representing a texture."""

    mcmeta: Optional[JsonDict] = extra_field(default=None)

    scope: ClassVar[tuple[str, ...]] = ("textures",)
    extension: ClassVar[str] = ".png"

    def bind(self, pack: "ResourcePack", path: str):
        super().bind(pack, path)

        if self.mcmeta is not None:
            pack.textures_mcmeta[path] = TextureMcmeta(self.mcmeta)


@dataclass(eq=False, repr=False)
class Sound(RawBinaryFileBase):
    """Class representing a sound file."""

    event: Optional[str] = extra_field(default=None)
    subtitle: Optional[str] = extra_field(default=None)
    replace: Optional[bool] = extra_field(default=None)
    volume: Optional[float] = extra_field(default=None)
    pitch: Optional[float] = extra_field(default=None)
    weight: Optional[int] = extra_field(default=None)
    stream: Optional[bool] = extra_field(default=None)
    attenuation_distance: Optional[int] = extra_field(default=None)
    preload: Optional[bool] = extra_field(default=None)

    scope: ClassVar[tuple[str, ...]] = ("sounds",)
    extension: ClassVar[str] = ".ogg"

    def bind(self, pack: "ResourcePack", path: str):
        super().bind(pack, path)

        namespace, _, path = path.partition(":")

        if self.event is not None:
            attributes = {
                "volume": self.volume,
                "pitch": self.pitch,
                "weight": self.weight,
                "stream": self.stream,
                "attenuation_distance": self.attenuation_distance,
                "preload": self.preload,
            }

            attributes = {k: v for k, v in attributes.items() if v is not None}
            event: JsonDict = {
                "sounds": [{"name": path, **attributes} if attributes else path]
            }

            if self.replace is not None:
                event["replace"] = self.replace
            if self.subtitle is not None:
                event["subtitle"] = self.subtitle

            pack[namespace].extra.merge(
                {"sounds.json": SoundConfig({self.event: event})}
            )


class SoundConfig(JsonFileBase[JsonDict]):
    """Class representing the sounds.json configuration."""

    def merge(self, other: "SoundConfig") -> bool:
        for key, other_event in other.data.items():
            if other_event.get("replace"):
                self.data[key] = deepcopy(other_event)
                continue

            event = self.data.setdefault(key, {})

            if subtitle := other_event.get("subtitle"):
                event["subtitle"] = subtitle

            sounds = event.setdefault("sounds", [])
            for sound in other_event.get("sounds", []):
                if sound not in sounds:
                    sounds.append(deepcopy(sound))

        return True


class Particle(JsonFileBase[JsonDict]):
    """Class representing a particle configuration file."""

    scope: ClassVar[tuple[str, ...]] = ("particles",)
    extension: ClassVar[str] = ".json"


class Atlas(JsonFileBase[JsonDict]):
    """Class representing an atlas configuration file."""

    scope: ClassVar[tuple[str, ...]] = ("atlases",)
    extension: ClassVar[str] = ".json"

    def merge(self, other: "Atlas") -> bool:  # type: ignore
        values = self.data.setdefault("sources", [])

        for value in other.data.get("sources", []):
            if value not in values:
                values.append(deepcopy(value))
        return True

    def append(self, other: "Atlas"):
        """Append values from another atlas."""
        self.merge(other)

    def prepend(self, other: "Atlas"):
        """Prepend values from another atlas."""
        values = self.data.setdefault("sources", [])

        for value in other.data.get("sources", []):
            if value not in values:
                values.insert(0, deepcopy(value))

    def add(self, value: JsonDict):
        """Add an entry."""
        values = self.data.setdefault("sources", [])
        if value not in values:
            values.append(value)

    def remove(self, value: JsonDict):
        """Remove an entry."""
        values = self.data.setdefault("sources", [])
        with suppress(ValueError):
            values.remove(value)

    @classmethod
    def default(cls) -> JsonDict:
        return {"sources": []}


class ResourcePackNamespace(Namespace):
    """Class representing a resource pack namespace."""

    directory = "assets"

    sound_config: ExtraPin[Optional[SoundConfig]] = ExtraPin(
        "sounds.json", default=None
    )

    # fmt: off
    blockstates:      NamespacePin[Blockstate]     = NamespacePin(Blockstate)
    models:           NamespacePin[Model]          = NamespacePin(Model)
    languages:        NamespacePin[Language]       = NamespacePin(Language)
    fonts:            NamespacePin[Font]           = NamespacePin(Font)
    glyph_sizes:      NamespacePin[GlyphSizes]     = NamespacePin(GlyphSizes)
    true_type_fonts:  NamespacePin[TrueTypeFont]   = NamespacePin(TrueTypeFont)
    shader_posts:     NamespacePin[ShaderPost]     = NamespacePin(ShaderPost)
    shaders:          NamespacePin[Shader]         = NamespacePin(Shader)
    fragment_shaders: NamespacePin[FragmentShader] = NamespacePin(FragmentShader)
    vertex_shaders:   NamespacePin[VertexShader]   = NamespacePin(VertexShader)
    glsl_shaders:     NamespacePin[GlslShader]     = NamespacePin(GlslShader)
    texts:            NamespacePin[Text]           = NamespacePin(Text)
    textures_mcmeta:  NamespacePin[TextureMcmeta]  = NamespacePin(TextureMcmeta)
    textures:         NamespacePin[Texture]        = NamespacePin(Texture)
    sounds:           NamespacePin[Sound]          = NamespacePin(Sound)
    particles:        NamespacePin[Particle]       = NamespacePin(Particle)
    atlases:          NamespacePin[Atlas]          = NamespacePin(Atlas)
    # fmt: on

    @classmethod
    def get_extra_info(cls) -> dict[str, type[PackFile]]:
        return {**super().get_extra_info(), "sounds.json": SoundConfig}


class ResourcePack(Pack[ResourcePackNamespace]):
    """Class representing a resource pack."""

    default_name = "untitled_resource_pack"

    pack_format_registry = {
        (1, 6): 1,
        (1, 7): 1,
        (1, 8): 1,
        (1, 9): 2,
        (1, 10): 2,
        (1, 11): 3,
        (1, 12): 3,
        (1, 13): 4,
        (1, 14): 4,
        (1, 15): 5,
        (1, 16): 6,
        (1, 17): 7,
        (1, 18): 8,
        (1, 19): 9,
    }
    latest_pack_format = pack_format_registry[split_version(LATEST_MINECRAFT_VERSION)]

    language_config = McmetaPin[dict[str, JsonDict]]("language", default_factory=dict)

    # fmt: off
    blockstates:      NamespaceProxyDescriptor[Blockstate]     = NamespaceProxyDescriptor(Blockstate)
    models:           NamespaceProxyDescriptor[Model]          = NamespaceProxyDescriptor(Model)
    languages:        NamespaceProxyDescriptor[Language]       = NamespaceProxyDescriptor(Language)
    fonts:            NamespaceProxyDescriptor[Font]           = NamespaceProxyDescriptor(Font)
    glyph_sizes:      NamespaceProxyDescriptor[GlyphSizes]     = NamespaceProxyDescriptor(GlyphSizes)
    true_type_fonts:  NamespaceProxyDescriptor[TrueTypeFont]   = NamespaceProxyDescriptor(TrueTypeFont)
    shader_posts:     NamespaceProxyDescriptor[ShaderPost]     = NamespaceProxyDescriptor(ShaderPost)
    shaders:          NamespaceProxyDescriptor[Shader]         = NamespaceProxyDescriptor(Shader)
    fragment_shaders: NamespaceProxyDescriptor[FragmentShader] = NamespaceProxyDescriptor(FragmentShader)
    vertex_shaders:   NamespaceProxyDescriptor[VertexShader]   = NamespaceProxyDescriptor(VertexShader)
    glsl_shaders:     NamespaceProxyDescriptor[GlslShader]     = NamespaceProxyDescriptor(GlslShader)
    texts:            NamespaceProxyDescriptor[Text]           = NamespaceProxyDescriptor(Text)
    textures_mcmeta:  NamespaceProxyDescriptor[TextureMcmeta]  = NamespaceProxyDescriptor(TextureMcmeta)
    textures:         NamespaceProxyDescriptor[Texture]        = NamespaceProxyDescriptor(Texture)
    sounds:           NamespaceProxyDescriptor[Sound]          = NamespaceProxyDescriptor(Sound)
    particles:        NamespaceProxyDescriptor[Particle]       = NamespaceProxyDescriptor(Particle)
    atlases:          NamespaceProxyDescriptor[Atlas]          = NamespaceProxyDescriptor(Atlas)
    # fmt: on
