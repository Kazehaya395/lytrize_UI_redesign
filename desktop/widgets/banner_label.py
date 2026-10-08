"""desktop/widgets/banner_label.py -- A logo/banner image that scales with the
window.

Why a widget at all, rather than a ``QLabel`` with a pixmap set once
-------------------------------------------------------------------------
The banner has to FILL the space it is given and follow the window as it is
resized. A pixmap assigned to a plain ``QLabel`` is a fixed-size image: the
label's size hint is the image's pixel size, so on a wide window the banner
stays 1080px and leaves a gap beside it, and on a narrow window the layout
either overflows or squashes the label without touching the image. Neither is
"stretched and auto-adjusting".

So the original pixmap is kept here, unscaled, and the *displayed* copy is
re-derived from it on every resize: scaled to the available width with
``Qt.KeepAspectRatio`` (so the artwork is never distorted, only scaled) and
``Qt.SmoothTransformation`` (so it does not alias into a staircase when
enlarged).

Two fill modes
---------------
``fit`` (the default) scales the image down until the WHOLE thing fits inside
the box, so any surplus shows as empty widget background. ``cover`` scales it
up until the box is filled in both axes and clips the overflow -- the CSS
``object-fit: cover`` rule. ``adapt`` is the mode that satisfies "show me all of
it, auto-adjust it, and never change its proportions" at the same time: instead
of forcing a foreign-shaped box onto the artwork, the widget RESIZES ITSELF so
its box always has the artwork's own aspect ratio, then paints the image to
fill that box exactly.

Why not just use "cover"
------------------------
Cover answers a different question. Given a wide, short band it fills the band
by cropping the top and the bottom off the artwork, which is precisely the
report it was meant to fix -- on a full screen the banner lost its shield, its
axis labels and both ends of the chart. Cover is right for a decorative photo
in a fixed slot; it is wrong for a logo whose whole content matters, because
"no gaps" is bought with "missing content". A fit render in the same wide band
leaves the opposite defect: a wide empty strip beside a small image.

``adapt`` gets both properties at once by making the BOX follow the IMAGE
rather than the image follow the box. Because the box's aspect always equals
the artwork's, the "scale to fill the box" step degenerates to a plain scale --
nothing is cropped and no empty strip is possible, not at one window size but at
every one. The widget is clamped to a height band so it stays a header element,
and it is never allowed to grow wider than the slot it was handed, so the page
cannot be pushed sideways on a narrow window.

Two deliberate limits, both applying to ``fit``
------------------------------------------------
* **Never upscaled past its native width.** A 1080px PNG blown up to 2000px is
  visibly soft, and a banner that is blurry is worse than a banner that is
  small. Enlarging is allowed only while the target is at or below native.
* **No minimum size.** The banner is allowed to shrink all the way out on a
  very narrow window rather than forcing a horizontal scrollbar onto a page
  whose whole job is to be narrow-friendly. The height follows from the aspect
  ratio, so the layout never has to be told a fixed height that would be wrong
  at the next resize.
"""

from __future__ import annotations

import logging
import math

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QLabel, QSizePolicy

log = logging.getLogger(__name__)


class BannerLabel(QLabel):
    """An image that fills the width available and rescales on every resize.

    Falls back to an empty, zero-height label when the file is missing or
    unreadable, so a packaging mistake shows as "no banner" rather than a
    broken-image placeholder or an exception during page construction.
    """

    def __init__(self, path: str, parent=None,
                 max_height: int = 300,
                 fill: str = "fit",
                 min_height: int = 120,
                 min_width: int = 160) -> None:
        super().__init__(parent)
        self._source = QPixmap()
        self._loaded_ok = False
        # How the artwork is mapped onto the widget's box.
        #   "fit"   -- scale down until the WHOLE image fits inside the box,
        #              preserving aspect ratio, and centre it. Whatever is left
        #              over shows as empty widget background.
        #   "cover" -- scale up until the box is completely covered (both
        #              dimensions full) and let the overflow spill past the
        #              edges, which the widget clips. Nothing is left empty.
        #   "adapt" -- keep the whole image visible at its own aspect ratio by
        #              resizing the widget to match the artwork, then filling
        #              that box exactly. No crop, no empty strip.
        # "fit" is the default so a plain banner still behaves conservatively;
        # the Home header asks for "adapt" because there the banner must be
        # complete AND free of gaps at every window size.
        mode = str(fill).lower()
        self._fill = mode if mode in ("cover", "adapt") else "fit"
        # The height ceiling used by "fit" mode. Without it the banner is 1080x462 natively, so at
        # full width it would stand 462px tall -- over half the height of a
        # 900px window, pushing the greeting, the stat cards and the sessions
        # list off the screen to make room for decoration. The cap only binds on
        # a genuinely wide window: at 1080px wide it still renders the full
        # 462px, and it is the HEIGHT limit that stops the artwork growing
        # without bound as the window widens.
        self._max_height = max(1, int(max_height))
        # A floor for "cover" mode, so the box is tall enough to read as a
        # banner rather than a thin strip. Ignored by "fit" mode, which derives
        # its height from the aspect ratio and must not be second-guessed.
        self._min_height = max(1, int(min_height))
        # The narrowest slot worth rendering artwork in. Only "adapt" consults
        # it; the other two always have something to draw in whatever box they
        # are given. See _adapt() for why the floor collapses the banner instead
        # of squeezing it.
        self._min_width = max(1, int(min_width))
        # Ignored width: this widget is happy to be given less than its natural
        # size and will rescale into whatever it gets, which is the entire
        # point. Fixed height: the height is DERIVED from the aspect ratio and
        # set explicitly by _rescale, so the layout must not second-guess it.
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        if self._fill == "cover":
            # "cover" needs a real box in BOTH axes to cover, so it must be
            # willing to be given height as well as width -- otherwise the layout
            # hands it a single line and the image has nothing to fill.
            self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        elif self._fill == "adapt":
            # "adapt" derives BOTH axes from the artwork: it takes whatever width
            # the layout offers, then resizes itself to the matching height. It
            # therefore wants to be stretched horizontally (so the slot's width
            # reaches it and the banner can grow with the window) but must not be
            # given a height of its own, because the height is what it computes.
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAlignment(Qt.AlignCenter)
        # Transparent artwork on a themed page: no frame, no background, and
        # no minimum height, so the PNG's own alpha is what shows.
        self.setStyleSheet("background: transparent; border: none;")
        if path:
            self._loaded_ok = self.load(path)
        if not self._loaded_ok:
            self.setFixedHeight(0)
            log.warning("welcome banner could not be loaded: %s", path)
        elif self._fill == "cover":
            # "cover" takes its height from the layout instead of deriving it, so
            # only the bounds are stated here -- never a fixed height, which would
            # make Qt re-run resizeEvent on every _cover() pass without the
            # geometry ever settling.
            #
            # The MAXIMUM matters as much as the minimum: with an Expanding
            # vertical policy and no ceiling the header row handed this widget
            # every spare pixel on the page (~480px), so a decorative band pushed
            # the stat cards and the sessions list off the screen. It is bounded
            # so the header stays a header.
            self.setMinimumHeight(self._min_height)
            self.setMaximumHeight(self._max_height)
        elif self._fill == "adapt":
            # "adapt" also takes its height from itself rather than from the
            # layout, so no minimum/maximum HEIGHT is set here -- _adapt() applies
            # both bounds itself as part of computing the geometry. A minimum
            # width is still worth stating so a very narrow window cannot squeeze
            # the banner into an unreadable sliver, and the horizontal policy is
            # what lets it shrink toward that floor instead of overflowing.
            self.setMinimumWidth(1)

    @property
    def _aspect(self) -> float:
        """The artwork's width / height, or 0.0 when there is no artwork."""
        src = self._source
        if src.isNull() or not src.height():
            return 0.0
        return src.width() / src.height()

    def load(self, path: str) -> bool:
        """Load *path* as the unscaled source image. False if unreadable."""
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            return False
        self._source = pixmap
        self._rescale()
        return True

    @property
    def has_image(self) -> bool:
        return not self._source.isNull()

    def _rescale(self) -> None:
        """Re-derive the displayed pixmap from the source for the current width.

        Guarded against re-entry: ``setFixedHeight`` resizes the widget, which
        calls ``resizeEvent``, which calls this again. Without the guard the
        second pass recomputes the same height and stops changing -- Qt simply
        does not emit a resize for an unchanged geometry -- but the guard makes
        the termination obvious instead of relying on that.
        """
        if self._source.isNull():
            return
        if self._fill == "cover":
            self._cover()
            return
        if self._fill == "adapt":
            self._adapt()
            return
        available = max(1, self.width())
        native_w = self._source.width()
        # Fill the width offered, but never upscale past native and never exceed
        # the height ceiling -- whichever binds first wins. `target_w` is derived
        # from whichever of those two limits is tighter, so the displayed image
        # is always <= the available box in BOTH axes and the aspect ratio is
        # preserved by construction (no separate target_h to disagree with it).
        by_height = int(native_w * self._max_height / self._source.height()) \
            if self._source.height() else native_w
        target_w = max(1, min(available, native_w, by_height))
        src = self._source.size()
        # Guard the division: a zero-width source would otherwise raise.
        target_h = (max(1, round(target_w * src.height() / src.width()))
                    if src.width() else 1)
        self.setFixedHeight(target_h)
        scaled = self._source.scaled(
            target_w, target_h,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        if scaled.size() != self.pixmap().size() or self.pixmap().isNull():
            self.setPixmap(scaled)

    def _cover(self) -> None:
        """Scale the artwork so it fills the widget's box in both axes.

        This is the ``object-fit: cover`` rule: the scale factor is the LARGER
        of the two axis ratios, so the image always spans the whole box and the
        excess spills past an edge instead of leaving a gap. The aspect ratio is
        never altered -- only the crop changes -- so unlike a naive stretch the
        artwork still looks correct, it is just cropped.

        Because the scaled image is at least as large as the widget in both
        axes and ``QLabel`` centres its pixmap, the overflow is clipped evenly
        on the opposing sides rather than dumped in one corner.
        """
        box_w, box_h = self.width(), self.height()
        if box_w <= 0 or box_h <= 0:
            return
        src = self._source
        sw, sh = src.width(), src.height()
        if sw <= 0 or sh <= 0:
            return
        # The larger ratio wins so the box is covered, not merely touched on one
        # side. Both dimensions are rounded UP (math.ceil, not round) and the
        # result is scaled with IgnoreAspectRatio: because both target edges come
        # from the SAME scale factor, the ratio is preserved by construction, and
        # handing Qt an exact integer size stops it re-deriving one internally.
        #
        # The ceil matters. Passing KeepAspectRatio here let Qt re-round the
        # requested size and return a pixmap 1px narrower than the box on some
        # widths, which showed as a hairline seam of background down one side --
        # the exact defect this mode exists to eliminate.
        scale = max(box_w / sw, box_h / sh)
        target_w = max(box_w, math.ceil(sw * scale))
        target_h = max(box_h, math.ceil(sh * scale))
        scaled = src.scaled(target_w, target_h,
                            Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        if scaled.size() != self.pixmap().size() or self.pixmap().isNull():
            self.setPixmap(scaled)

    def _adapt(self) -> None:
        """Resize the WIDGET to the artwork's aspect ratio, then fill it.

        This is the mode that answers "auto-adjust it, keep the same aspect
        ratio, and stop clipping it". Every other mode maps the image onto a box
        somebody else chose; here the box is derived from the image instead, so
        the two failure modes cannot both happen:

        * the artwork is never cropped, because nothing is ever scaled past the
          box -- the box is exactly the artwork's shape;
        * no empty strip can appear beside or above the artwork, because the box
          is exactly as big as the artwork.

        The width comes from the layout (that is what makes the banner grow and
        shrink with the window) and the height is computed from it, then clamped
        into the configured height band so the banner stays a header element.
        The clamp can leave the widget narrower than the slot offered, which is
        correct: the surplus becomes page background outside the widget rather
        than a letterbox inside it, and the artwork is still whole.

        Re-entrancy is the thing to watch. ``resize()`` re-enters
        ``resizeEvent`` -> ``_rescale`` -> here, so the geometry is only applied
        when it actually differs; otherwise every pass would schedule another.
        """
        aspect = self._aspect
        if aspect <= 0.0:
            return
        slot_w = self.width()
        if slot_w <= 0:
            return
        # Below a certain slot there is no width worth showing artwork in: the
        # greeting and the Start button need the room more than a 40px sliver of
        # a logo does. Collapse to nothing rather than rendering a speck -- an
        # unreadable fragment reads as a broken image, whereas no banner reads as
        # "there wasn't room". Widening the window brings it straight back,
        # because this is re-evaluated on every resize.
        if slot_w < self._min_width:
            if self.width() or self.height():
                self.resize(0, 0)
            if not self.pixmap().isNull():
                self.clear()
            return
        # Height that this width implies, clamped into the header band. Both
        # edges are rounded so the result is a whole number of pixels -- Qt has
        # no use for a fractional widget height and would round it a second time,
        # breaking the aspect ratio by a pixel.
        target_h = max(self._min_height,
                       min(self._max_height, int(round(slot_w / aspect))))
        # The matching width for that height. On a window wide enough for the
        # height cap to bind, this is NARROWER than the slot; the widget is then
        # left narrower than the space it was offered, which is exactly the point
        # -- it stops instead of cropping.
        target_w = max(1, int(round(target_h * aspect)))
        # Never exceed the slot. If the artwork at this height would be wider than
        # the space available (a narrow window where the height FLOOR binds), fall
        # back to matching the width and take the shorter height, so the page is
        # never pushed sideways.
        if target_w > slot_w:
            target_w = slot_w
            target_h = max(1, int(round(target_w / aspect)))
        if self.width() != target_w or self.height() != target_h:
            self.resize(target_w, target_h)
        # The box now matches the artwork, so filling it is a plain scale. The
        # size is read back from the widget rather than reused from the targets:
        # Qt may settle the geometry a pixel differently from what was asked for
        # (the size hint of a pixmap-bearing label feeds back into it), and
        # scaling to a stale target is what leaves a 1-2px strip of background
        # showing along one edge.
        #
        # IgnoreAspectRatio is safe *only* because the box was derived from the
        # artwork's aspect a line above: the two can disagree by at most the
        # rounding of a single pixel, not by any real distortion. Asking Qt to
        # re-derive the ratio with KeepAspectRatio is what caused the strip,
        # because it rounds DOWN to whichever axis binds and leaves the other
        # edge uncovered.
        box_w, box_h = max(1, self.width()), max(1, self.height())
        scaled = self._source.scaled(
            box_w, box_h,
            Qt.IgnoreAspectRatio,
            Qt.SmoothTransformation,
        )
        if scaled.size() != self.pixmap().size() or self.pixmap().isNull():
            self.setPixmap(scaled)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        # In "cover" mode the widget must paint only INSIDE its own bounds --
        # the scaled pixmap is deliberately larger than the box, and without an
        # explicit clip Qt would let it bleed over the page title and the Start
        # button next to it. "adapt" is clipped for the opposite, much milder
        # reason: its pixmap is sized to the box, but integer rounding can still
        # leave it a pixel wider or taller, and a one-pixel overhang onto the page
        # background is still an overhang.
        if self._fill not in ("cover", "adapt") or self._source.isNull():
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.setClipRect(self.rect())
        # QLabel would otherwise also draw its own frame/background; drawing the
        # pixmap here keeps the centring identical to the base implementation
        # while respecting the clip.
        pm = self.pixmap()
        if pm.isNull():
            painter.end()
            super().paintEvent(event)
            return
        x = (self.width() - pm.width()) // 2
        y = (self.height() - pm.height()) // 2
        painter.drawPixmap(x, y, pm)
        painter.end()

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        self._rescale()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        # The first real geometry arrives at show time, not at construction, so
        # without this the banner would keep its unscaled size until the window
        # was resized by hand.
        super().showEvent(event)
        self._rescale()