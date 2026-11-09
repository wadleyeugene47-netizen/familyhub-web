from django.db import models
from django.utils.text import slugify

# Addresses already used by other pages under /communities/.
RESERVED_SLUGS = {"create"}


def unique_slug(model, name, *, exclude_pk=None):
    base = slugify(name)[:170] or "community"
    candidate, counter = base, 1
    while candidate in RESERVED_SLUGS or model.objects.filter(slug=candidate).exclude(pk=exclude_pk).exists():
        counter += 1
        candidate = f"{base}-{counter}"
    return candidate


class Community(models.Model):
    """A group inside the site. Its leagues, tournaments, events and All-Star games live on one page."""

    organizer = models.ForeignKey(
        "organizers.OrganizerProfile",
        on_delete=models.PROTECT,
        related_name="communities",
    )
    name = models.CharField(max_length=160)
    slug = models.SlugField(max_length=180, unique=True, editable=False)
    tagline = models.CharField(max_length=160, blank=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "communities"
        constraints = [
            models.UniqueConstraint(fields=["organizer", "name"], name="unique_community_name_per_organizer"),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug(Community, self.name, exclude_pk=self.pk)
        super().save(*args, **kwargs)

    def is_organized_by(self, user):
        return user.is_authenticated and self.organizer.user_id == user.pk

    def can_manage(self, user):
        return user.is_authenticated and (user.is_superuser or self.organizer.user_id == user.pk)
