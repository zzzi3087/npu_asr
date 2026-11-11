"""One visibility policy for history and transparent desktop captions."""

def caption_visibility(settings):
    return tuple(settings.get(key,True) if type(settings.get(key,True)) is bool else True
        for key in ('show_source','show_translation'))


def visible_caption_pair(source,translation,show_source=True,show_translation=True,*,status=None,show_pending_source=False):
    source=str(source or '').strip();translation=str(translation or '').strip()
    translated=translation if show_translation else ''
    # Pending-source fallback is opt in; confirmed service failures remain readable.
    fallback=show_translation and not translation and (status=='failed' or show_pending_source)
    original=source if show_source or fallback else ''
    return original,translated
