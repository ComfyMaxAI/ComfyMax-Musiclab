"""Viewport follows the device position; never writes to the audio transport."""


def follow_start(position, start, span, duration, fraction=.45):
    if position < start:  # backward seek / loop wrap
        return max(0., min(duration-span, position-span*fraction))
    return max(0., min(max(0., duration-span), max(start, position-span*fraction)))
