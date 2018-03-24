from django.core.management.base import BaseCommand

from imagefield.fields import IMAGE_FIELDS


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            dest='force',
            help='Force processing of images even if they exist already.',
        )
        parser.add_argument(
            'field',
            nargs='*',
            type=str,
            help='Process only some fields (app.Model.field or app.Model).',
        )

        # TODO --clear for removing previously generated images.

    def _make_filter(self, fields):
        if not fields:
            self._filter = None

        self._filter = {}
        for field in fields:
            parts = field.lower().split('.')
            self._filter['.'.join(parts[:2])] = parts[2:]

    def _skip_field(self, field):
        if self._filter is None:
            # Process all fields
            return False
        fields = self._filter.get(field.model._meta.label_lower)
        return fields is None or (fields and field.name not in fields)

    def handle(self, **options):
        self._make_filter(options['field'])

        for field in IMAGE_FIELDS:
            if self._skip_field(field):
                continue

            self.stdout.write('%s: %s' % (
                field.model._meta.label,
                field.name,
            ))

            queryset = field.model._default_manager.all()
            count = queryset.count()
            for index, instance in enumerate(queryset):
                fieldfile = getattr(instance, field.name)
                if fieldfile and fieldfile.name:
                    for key in field.formats:
                        try:
                            fieldfile.process(key, force=options.get('force'))
                        except Exception:
                            pass

                if index % 10 == 0:
                    progress = '*' * int(index / count * 50)
                    self.stdout.write('\r|%s|' % progress.rjust(50), ending='')
            self.stdout.write('\r|%s|' % ('*' * 50,))
