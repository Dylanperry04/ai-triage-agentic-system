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

export function notificationDestination(notification, rolesInput, navItems) {
  const roles = new Set(rolesInput || []);
  const nav = new Set(navItems || []);
  const first = (navItems || [])[0] || null;
  if (notification?.kind === "monthly_retraining") {
    return { tab: nav.has("itd") ? "itd" : first, selection: "none" };
  }
  if (["recheck", "information_request"].includes(notification?.kind)) {
    return { tab: nav.has("triage") ? "triage" : first, selection: "selected" };
  }
  if (notification?.kind === "triage_review" && roles.has("triage_nurse")) {
    return { tab: nav.has("triage") ? "triage" : first, selection: "selected" };
  }
  if (notification?.kind === "triage_review" && roles.has("ed_doctor")) {
    return { tab: nav.has("escalations") ? "escalations" : first, selection: "focus" };
  }
  return {
    tab: nav.has("escalations") ? "escalations" : nav.has("review") ? "review" : first,
    selection: "focus",
  };
}
