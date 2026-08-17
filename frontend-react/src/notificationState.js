export const notificationAnnouncementKey = (notification) =>
  notification.semanticKey || notification.id;

export function reconcileNotificationSnapshot(next, announced, baselineLoaded) {
  const ordered = [...next].sort((a, b) => Number(b.at || 0) - Number(a.at || 0));
  const updated = new Set(announced || []);
  const newlyUnread = baselineLoaded
    ? ordered.filter(
        (notification) =>
          !notification.read && !updated.has(notificationAnnouncementKey(notification)),
      )
    : [];
  ordered.forEach((notification) =>
    updated.add(notificationAnnouncementKey(notification)),
  );
  return { ordered, announced: updated, newlyUnread };
}

export function mergeNotificationFallback(durable, fallback) {
  const durableKeys = new Set(durable.map(notificationAnnouncementKey));
  return [
    ...durable,
    ...fallback.filter(
      (notification) => !durableKeys.has(notificationAnnouncementKey(notification)),
    ),
  ];
}
