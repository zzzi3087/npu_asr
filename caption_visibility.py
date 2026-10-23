"""One visibility policy for history and transparent desktop captions."""

def caption_visibility(settings):
    return tuple(settings.get(key,True) if type(settings.get(key,True)) is bool else True
        for key in ('show_source','show_translation'))


def visible_caption_pair(source,translation,show_source=True,show_translation=True):
    source=str(source or '').strip();translation=str(translation or '').strip()
    translated=translation if show_translation else ''
    # A missing translation must not erase the only readable caption. An
    # explicit choice to hide both languages still hides all caption content.
    original=source if show_source or (show_translation and not translated) else ''
    return original,translated
