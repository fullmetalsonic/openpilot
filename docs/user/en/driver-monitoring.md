# Driver monitoring

Set `DisableDM` in Carrot Web under Vehicle and hardware > Driver monitoring. The default is `0`; reboot the device after changing it.

| Value | Behavior |
|---|---|
| `0` | Uses the existing driver-camera model and monitoring policy. |
| `1` | Disables driver monitoring without enabling web road video. |
| `2` | Disables driver monitoring and permits web road video (WebRTC). Web video remains unavailable while `ClusterHud` is enabled. |

`DisableDM` is included in file backups, QR backups and setting profiles, and is restored with them. It is not migrated into another monitoring setting. Three CANCEL presses do not change monitoring for the ignition session. The video option does not independently enable monitoring.

Driver View and AlwaysOnDM retain their existing behavior. Camera failure does not automatically select interaction monitoring. Driver warnings retain the existing volume controls.

Disabling monitoring removes the existing monitoring alerts and monitoring-triggered deceleration and lockout protections. Actual vehicle monitoring, warning and video behavior requires field validation. See the [Carrot Web guide](carrot-web.md) for web video.
