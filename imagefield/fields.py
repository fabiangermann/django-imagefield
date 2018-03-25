import hashlib
import io
import os
from types import SimpleNamespace

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import models
from django.db.models import signals
from django.db.models.fields import files
from django.forms import ClearableFileInput
from django.utils.functional import cached_property
from django.utils.http import urlsafe_base64_encode

from PIL import Image

from .processing import build_handler
from .widgets import PPOIWidget, with_preview_and_ppoi


IMAGE_FIELDS = []


def urlhash(str):
    digest = hashlib.sha1(str.encode('utf-8')).digest()
    return urlsafe_base64_encode(digest).decode('ascii')


class ImageFieldFile(files.ImageFieldFile):
    def __getattr__(self, item):
        if item in self.field.formats:
            url = self.storage.url(
                self._processed_name(self.field.formats[item]),
            )
            setattr(self, item, url)
            return url
        raise AttributeError

    def _ppoi(self):
        if self.field.ppoi_field:
            return [
                float(coord) for coord in
                getattr(self.instance, self.field.ppoi_field).split('x')
            ]
        return [0.5, 0.5]

    def _processed_name(self, processors):
        p1 = urlhash(self.name)
        p2 = urlhash(
            '|'.join(str(p) for p in processors) + '|' + str(self._ppoi()),
        )
        _, ext = os.path.splitext(self.name)

        return '__processed__/%s/%s_%s%s' % (p1[:2], p1[2:], p2, ext)

    def _processed_base(self):
        p1 = urlhash(self.name)
        return '__processed__/%s' % p1[:2], '%s_' % p1[2:]

    def process(self, item, force=False):
        processors = self.field.formats[item]
        target = self._processed_name(processors)
        if not force and self.storage.exists(target):
            return

        with self.open('rb') as orig:
            image = Image.open(orig)
            context = SimpleNamespace(
                ppoi=self._ppoi(),
                save_kwargs={},
            )
            format = image.format
            _, ext = os.path.splitext(self.name)

            handler = build_handler(processors)
            image, context = handler(image, context)

            with io.BytesIO() as buf:
                image.save(buf, format=format, **context.save_kwargs)

                self.storage.delete(target)
                self.storage.save(target, ContentFile(buf.getvalue()))

                print('Saved', target)


class ImageField(models.ImageField):
    attr_class = ImageFieldFile

    def __init__(self, verbose_name=None, **kwargs):
        self._formats = kwargs.pop('formats', {})
        self.ppoi_field = kwargs.pop('ppoi_field', None)

        # TODO implement this? Or handle this outside? Maybe as an image
        # processor? I fear that otherwise we have to reimplement parts of the
        # ImageFileDescriptor (not hard, but too much copy paste for my taste)
        # self.placeholder = kwargs.pop('placeholder', None)

        super().__init__(verbose_name, **kwargs)

        IMAGE_FIELDS.append(self)

    @cached_property
    def formats(self):
        setting = getattr(settings, 'IMAGEFIELD_FORMATS', {})
        return setting.get(
            ('%s.%s' % (self.model._meta.label_lower, self.name)).lower(),
            self._formats,
        )

    def contribute_to_class(self, cls, name, **kwargs):
        super().contribute_to_class(cls, name, **kwargs)

        if not cls._meta.abstract:
            # TODO Avoid calling process() too often?
            # signals.post_init.connect(self.cache_values, sender=cls)

            # TODO Allow deactivating this by to move it out of the
            # request-response cycle.
            signals.post_save.connect(
                self._generate_files,
                sender=cls,
            )
            signals.post_delete.connect(
                self._clear_generated_files,
                sender=cls,
            )

    def formfield(self, **kwargs):
        kwargs['widget'] = with_preview_and_ppoi(
            kwargs.get('widget', ClearableFileInput),
            ppoi_field=self.ppoi_field,
        )
        return super().formfield(**kwargs)

    def save_form_data(self, instance, data):
        super().save_form_data(instance, data)

        # Reset PPOI field if image field is cleared
        if data is not None and not data:
            if self.ppoi_field:
                setattr(instance, self.ppoi_field, '0.5x0.5')

    def _generate_files(self, instance, **kwargs):
        f = getattr(instance, self.name)
        for item in f.field.formats:
            f.process(item)

    @staticmethod
    def _clear_generated_files(instance, **kwargs):
        f = getattr(instance, self.name)
        folder, startswith = f._processed_name()
        for file in f.storage.listdir(folder):
            if file.startswith(startswith):
                f.storage.delete(file)


class PPOIField(models.CharField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault('default', '0.5x0.5')
        kwargs.setdefault('max_length', 20)
        super().__init__(*args, **kwargs)

    def formfield(self, **kwargs):
        kwargs['widget'] = PPOIWidget
        return super().formfield(**kwargs)
