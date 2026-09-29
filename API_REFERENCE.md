# DaVinci Resolve Scripting API — Reference (สำหรับ editortrack)

> **แหล่ง authoritative ตัวจริง = README ที่ติดมากับ Resolve** (ครบ + ตรงเวอร์ชันคุณเป๊ะ):
> ```bash
> cat "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/README.txt"
> ```
> เอกสารนี้คือ **ชุด method ที่ใช้บ่อย/เกี่ยวกับ tracker จัดหมวดไว้** — ไม่รับประกันว่าครบทุกตัวหรือตรงทุกเวอร์ชัน
> `probe.py` คือตัวยืนยันว่าเครื่อง+เวอร์ชันคุณมีอะไรจริง
>
> tag: 🟢 verify แล้ว (งานวิจัย/primary) · 🟡 รู้จาก doc/training · 🟠 ต้อง probe ยืนยัน

---

## 🚨 กฎความปลอดภัยของ tracker (อ่านก่อน)

**editortrack ใช้ได้เฉพาะ method อ่านอย่างเดียว: `Get*` / `Is*`**
ห้ามเรียกอะไรที่เปลี่ยนสถานะโปรเจคของ editor เด็ดขาด — ทุกตัวที่ขึ้นต้น/มีคำว่า
`Set / Add / Delete / Create / Import / Export / Start / Stop / Load / Save / Move / Append / Apply / Replace / Link`
= **mutating → ห้ามเรียกในตัว tracker** (เผลอเรียก `DeleteTimeline`/`StartRendering` = พังงานจริง)

> hierarchy: `Resolve → ProjectManager → Project → {Timeline → TimelineItem → Graph} / {MediaPool → Folder → MediaPoolItem} / Gallery`

---

## 1. Resolve (root)
| method | คืนอะไร | conf |
|---|---|---|
| `GetProjectManager()` | ProjectManager | 🟢 |
| `GetCurrentPage()` | หน้าที่เปิด: media/cut/edit/fusion/color/fairlight/deliver | 🟡 |
| `GetProductName()` | "DaVinci Resolve Studio" / "DaVinci Resolve" | 🟡 |
| `GetVersionString()` | เวอร์ชัน เช่น "20.3.0" | 🟡 |
| `GetMediaStorage()` | MediaStorage | 🟡 |
| `Fusion()` | Fusion object | 🟠 |

## 2. ProjectManager
| method | คืนอะไร | conf |
|---|---|---|
| `GetCurrentProject()` | Project | 🟢 |
| `GetProjectListInCurrentFolder()` | list ชื่อโปรเจค | 🟡 |
| `GetFolderListInCurrentFolder()` | list โฟลเดอร์ | 🟡 |
| `GetCurrentDatabase()` / `GetDatabaseList()` | ข้อมูล DB | 🟠 |

## 3. Project
| method | คืนอะไร | conf |
|---|---|---|
| `GetName()` | ชื่อโปรเจค | 🟢 |
| `GetTimelineCount()` | จำนวน timeline | 🟢 |
| `GetTimelineByIndex(i)` / `GetCurrentTimeline()` | Timeline | 🟢 |
| `IsRenderingInProgress()` | bool | 🟢 |
| `GetRenderJobList()` | list ของ job (dict) | 🟢 |
| `GetRenderJobStatus(jobId)` | dict: **CompletionPercentage**, JobStatus, เวลา | 🟢 |
| `GetCurrentRenderMode()` | โหมด render | 🟡 |
| `GetRenderResolutions()` / `GetRenderFormats()` / `GetRenderCodecs(fmt)` | ตัวเลือก render | 🟡 |
| `GetPresetList()` / `GetRenderPresetList()` | preset | 🟡 |
| `GetSetting(name)` | ค่า setting (ไม่ใส่ arg = ทั้งหมด): fps, resolution, color science | 🟡 |
| `GetMediaPool()` | MediaPool | 🟡 |
| `GetGallery()` | Gallery | 🟡 |
| `GetUniqueId()` | id | 🟠 |
| ⛔ mutating: `SetSetting, AddRenderJob, DeleteRenderJob, StartRendering, StopRendering, SetCurrentTimeline` | | |

## 4. Timeline
| method | คืนอะไร | conf |
|---|---|---|
| `GetName()` | ชื่อ | 🟢 |
| `GetStartFrame()` / `GetEndFrame()` | frame → duration | 🟢 |
| `GetStartTimecode()` / `GetCurrentTimecode()` | timecode | 🟡 |
| `GetTrackCount("video"/"audio"/"subtitle")` | จำนวน track | 🟢 |
| `GetItemListInTrack(type, i)` | list ของ clip (⚠️ ไม่รวม transition) | 🟢 |
| `GetTrackName(type, i)` | ชื่อ track | 🟡 |
| `GetTrackSubType("audio", i)` | mono/stereo/5.1 | 🟠 |
| `GetIsTrackEnabled/Locked(type, i)` | bool | 🟠 |
| `GetMarkers()` | dict {frame:{name,note,color,duration,customData}} | 🟢 |
| `GetCurrentVideoItem()` | TimelineItem ที่ playhead | 🟡 |
| `GetCurrentClipThumbnailImage()` | dict {width,height,format,**data=base64**} | 🟠 |
| `GetSetting(name)` | timeline setting | 🟡 |
| ⛔ mutating: `SetName, AddMarker, DeleteMarkerAtFrame, SetSetting, SetCurrentTimecode, ApplyGradeFromDRX` | | |

## 5. TimelineItem (clip บน timeline)
| method | คืนอะไร | conf |
|---|---|---|
| `GetName()` | ชื่อ clip | 🟢 |
| `GetStart()` / `GetEnd()` / `GetDuration()` | ตำแหน่ง/ยาว (frame) | 🟢 |
| `GetLeftOffset()` / `GetRightOffset()` | หัว/ท้ายที่ตัดออก | 🟡 |
| `GetSourceStartFrame()` / `GetSourceEndFrame()` | ช่วง source ที่ใช้ | 🟡 |
| `GetMediaPoolItem()` | MediaPoolItem (→ หมวด 6) | 🟡 |
| `GetProperty(key)` | transform/crop/opacity/composite (ไม่ใส่ key = ทั้งหมด) | 🟡 |
| `GetMarkers()` / `GetFlagList()` / `GetClipColor()` | marker/flag/สี ต่อ clip | 🟡/🟠 |
| `GetFusionCompCount()` / `GetFusionCompNameList()` | จำนวน/ชื่อ Fusion comp (VFX) | 🟡 |
| `GetNodeGraph()` | Graph object (สี → หมวด 5b) | 🟠 |
| `GetVersionNameList(0/1)` | color versions local/remote | 🟠 |
| `GetColorGroup()` | color group | 🟠 |
| ⛔ mutating: `SetProperty, AddMarker, AddVersion, SetLUT, AddFusionComp, DeleteVersionByName` | | |

### 5b. Graph (จาก `GetNodeGraph()` — ชั้นสี)
| method | คืนอะไร | conf |
|---|---|---|
| `GetNumNodes()` | จำนวน color node (default=1) | 🟠 |
| `GetNodeLabel(i)` | label ของ node | 🟠 |
| `GetToolsInNode(i)` / `GetLUT(i)` | tool/LUT ใน node | 🟠 |
| ⛔ mutating: `SetLUT, SetNodeEnabled, ApplyGradeFromDRX` | | |

## 6. MediaPool / Folder / MediaPoolItem (วัตถุดิบ)
| method | คืนอะไร | conf |
|---|---|---|
| `MediaPool.GetRootFolder()` / `GetCurrentFolder()` | Folder | 🟡 |
| `Folder.GetName()` / `GetClipList()` / `GetSubFolderList()` | โครง bin | 🟡 |
| `MediaPoolItem.GetName()` | ชื่อ | 🟡 |
| `MediaPoolItem.GetClipProperty(key)` | dict: Resolution, FPS, Video Codec, Duration, Frames, File Path, Type, Usage, Proxy, Date Created/Modified... | 🟡 |
| `MediaPoolItem.GetMetadata(key)` | camera/scene/shot/take/keyword/comment | 🟡 |
| `MediaPoolItem.GetMarkers()` / `GetFlagList()` | marker/flag ระดับ media | 🟡/🟠 |
| ⛔ mutating: `AppendToTimeline, CreateTimelineFromClips, DeleteClips, MoveClips, SetMetadata, SetClipProperty` | | |

## 7. Gallery (stills) — ใช้น้อย
| method | คืนอะไร | conf |
|---|---|---|
| `Gallery.GetGalleryStillAlbums()` / `GetCurrentStillAlbum()` | albums | 🟠 |
| `GalleryStillAlbum.GetStills()` / `GetLabel(still)` | รายการ still | 🟠 |
| ⛔ mutating: `ExportStills, DeleteStills` | | |

---

## editortrack ใช้อะไรบ้าง (สรุป mapping)

| ต้องการ | method |
|---|---|
| **metric #1 — ตัวเศษ** | `GetStartFrame/EndFrame` + `GetItemListInTrack` (นับ clip/ความยาว) + `GetRenderJobStatus.CompletionPercentage` |
| **เฟสงาน** | `GetCurrentPage()` |
| **สี (ถ้า probe ผ่าน)** | `GetNodeGraph().GetNumNodes()` หรือ `GetVersionNameList` |
| **audio coverage** | `GetItemListInTrack("audio",i)` + `GetStart/GetEnd` |
| **todo/สถานะจาก editor** | `GetMarkers()` (note) |
| **ภาพงาน (แทน screenshot?)** | `GetCurrentClipThumbnailImage()` 🟠 |
| **metric #2 — เวลา** | ❌ ไม่มีใน API → ใช้ macOS `CGEventSource` + `NSWorkspace` |

**ดึงไม่ได้:** "เสร็จ/ดีหรือยัง", ประวัติการแก้/timestamp/ใครแก้, เวลา active ของคน, transition (ไม่อยู่ใน `GetItemListInTrack`)
