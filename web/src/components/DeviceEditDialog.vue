<script setup lang="ts">
import { ref, watch, computed, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import { message } from 'ant-design-vue'
import { UploadOutlined, PictureOutlined, CheckOutlined, CloseOutlined, ZoomInOutlined, ZoomOutOutlined } from '@ant-design/icons-vue'
import { uploadFile } from '@/services/upload'
import { pushDisplayCommand } from '@/services/device'
import type { Device, Role } from '@/types/device'

const { t } = useI18n()

/** 默认屏幕尺寸，前端裁剪/缩放目标 */
const TARGET_WIDTH = 320
const TARGET_HEIGHT = 240
const TARGET_ASPECT = TARGET_WIDTH / TARGET_HEIGHT

interface Props {
  visible: boolean
  current: Device | null
  roleItems: Role[]
  clearMemoryLoading?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  clearMemoryLoading: false,
})

const emit = defineEmits<{
  close: []
  submit: [device: Device]
  clearMemory: [device: Device]
}>()

const formData = ref<Device>({
  deviceId: '',
  deviceName: '',
  roleId: undefined,
  state: '0',
})

// 背景图片相关状态
const backgroundPreview = ref<string>('')
const backgroundUploading = ref(false)
const backgroundPushing = ref(false)

// 裁剪编辑器状态
const cropperVisible = ref(false)
const sourceImage = ref<HTMLImageElement | null>(null)
const sourceImageUrl = ref('')
const sourceImageNatWidth = ref(0)
const sourceImageNatHeight = ref(0)

// 裁剪框在预览容器中的位置（像素），容器固定 480×360
const CONTAINER_WIDTH = 480
const CONTAINER_HEIGHT = 360
const cropScale = ref(1)       // 原图到容器的缩放比
const cropX = ref(0)           // 裁剪框左上角 X（容器坐标）
const cropY = ref(0)           // 裁剪框左上角 Y（容器坐标）
const cropW = ref(0)           // 裁剪框宽度（容器坐标）
const cropH = ref(0)           // 裁剪框高度（容器坐标）
const isDragging = ref(false)
const dragStartX = ref(0)
const dragStartY = ref(0)
const dragStartCropX = ref(0)
const dragStartCropY = ref(0)

const isDeviceOnline = computed(() => {
  return formData.value.state === '1' || formData.value.state === '2'
})

// 容器中图片的实际显示尺寸
const displayWidth = ref(0)
const displayHeight = ref(0)

function handleClose() {
  backgroundPreview.value = ''
  cropperVisible.value = false
  emit('close')
}

function handleOk() {
  emit('submit', formData.value)
}

function handleClearMemory() {
  emit('clearMemory', formData.value)
}

/**
 * 选择图片后打开裁剪编辑器
 */
function handleFileSelect(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  if (!file.type.startsWith('image/')) {
    message.error('请选择图片文件')
    return
  }

  const url = URL.createObjectURL(file)
  const img = new Image()
  img.onload = () => {
    sourceImage.value = img
    sourceImageUrl.value = url
    sourceImageNatWidth.value = img.naturalWidth
    sourceImageNatHeight.value = img.naturalHeight
    initCropper()
    cropperVisible.value = true
  }
  img.onerror = () => {
    URL.revokeObjectURL(url)
    message.error('图片加载失败')
  }
  img.src = url
  input.value = ''
}

/**
 * 初始化裁剪框：图片缩放到容器内，裁剪框居中且保持 4:3 比例
 */
function initCropper() {
  const natW = sourceImageNatWidth.value
  const natH = sourceImageNatHeight.value

  // 图片缩放到容器内（contain 模式）
  const scaleX = CONTAINER_WIDTH / natW
  const scaleY = CONTAINER_HEIGHT / natH
  cropScale.value = Math.min(scaleX, scaleY)

  displayWidth.value = natW * cropScale.value
  displayHeight.value = natH * cropScale.value

  // 裁剪框：尽可能大，保持 4:3 比例，居中
  const maxCropW = displayWidth.value
  const maxCropH = displayHeight.value
  if (maxCropW / maxCropH > TARGET_ASPECT) {
    cropH.value = maxCropH
    cropW.value = maxCropH * TARGET_ASPECT
  } else {
    cropW.value = maxCropW
    cropH.value = maxCropW / TARGET_ASPECT
  }
  cropX.value = (displayWidth.value - cropW.value) / 2
  cropY.value = (displayHeight.value - cropH.value) / 2
}

/**
 * 缩放裁剪框
 */
function zoomCrop(delta: number) {
  const step = 20
  const newW = Math.max(80, Math.min(displayWidth.value, cropW.value + delta * step * TARGET_ASPECT))
  const newH = newW / TARGET_ASPECT

  if (newH > displayHeight.value) return

  // 保持中心不变
  const centerX = cropX.value + cropW.value / 2
  const centerY = cropY.value + cropH.value / 2
  cropW.value = newW
  cropH.value = newH
  cropX.value = Math.max(0, Math.min(displayWidth.value - newW, centerX - newW / 2))
  cropY.value = Math.max(0, Math.min(displayHeight.value - newH, centerY - newH / 2))
}

/**
 * 拖动裁剪框
 */
function onCropMouseDown(event: MouseEvent) {
  isDragging.value = true
  dragStartX.value = event.clientX
  dragStartY.value = event.clientY
  dragStartCropX.value = cropX.value
  dragStartCropY.value = cropY.value
  document.addEventListener('mousemove', onCropMouseMove)
  document.addEventListener('mouseup', onCropMouseUp)
}

function onCropMouseMove(event: MouseEvent) {
  if (!isDragging.value) return
  const dx = event.clientX - dragStartX.value
  const dy = event.clientY - dragStartY.value
  cropX.value = Math.max(0, Math.min(displayWidth.value - cropW.value, dragStartCropX.value + dx))
  cropY.value = Math.max(0, Math.min(displayHeight.value - cropH.value, dragStartCropY.value + dy))
}

function onCropMouseUp() {
  isDragging.value = false
  document.removeEventListener('mousemove', onCropMouseMove)
  document.removeEventListener('mouseup', onCropMouseUp)
}

/**
 * 确认裁剪并上传推送
 */
async function confirmCrop() {
  if (!sourceImage.value) return

  try {
    backgroundUploading.value = true

    // 从裁剪框坐标换算回原图坐标
    const scale = cropScale.value
    const srcX = cropX.value / scale
    const srcY = cropY.value / scale
    const srcW = cropW.value / scale
    const srcH = cropH.value / scale

    // Canvas 裁剪并缩放到目标尺寸
    const canvas = document.createElement('canvas')
    canvas.width = TARGET_WIDTH
    canvas.height = TARGET_HEIGHT
    const ctx = canvas.getContext('2d')
    if (!ctx) { message.error('Canvas 不可用'); return }

    ctx.drawImage(sourceImage.value, srcX, srcY, srcW, srcH, 0, 0, TARGET_WIDTH, TARGET_HEIGHT)

    const blob = await new Promise<Blob>((resolve, reject) => {
      canvas.toBlob(
        (b) => { if (b) resolve(b); else reject(new Error('Canvas 转换失败')) },
        'image/png'
      )
    })

    const resizedFile = new File([blob], `background_${Date.now()}.png`, { type: 'image/png' })
    backgroundPreview.value = URL.createObjectURL(blob)
    cropperVisible.value = false

    // 上传到服务器
    const imageUrl = await uploadFile(resizedFile, 'image') as string
    if (!imageUrl) { message.error('图片上传失败'); return }

    // 推送到设备
    backgroundPushing.value = true
    const res = await pushDisplayCommand(formData.value.deviceId, 'set_background', { url: imageUrl })
    if (res.code === 200) {
      message.success('背景图片已推送到设备')
    } else {
      message.warning(res.message || '推送失败，设备可能不在线')
    }
  } catch (error) {
    console.error('背景图片处理失败:', error)
    message.error('背景图片处理失败')
  } finally {
    backgroundUploading.value = false
    backgroundPushing.value = false
    if (sourceImageUrl.value) {
      URL.revokeObjectURL(sourceImageUrl.value)
      sourceImageUrl.value = ''
    }
  }
}

function cancelCrop() {
  cropperVisible.value = false
  if (sourceImageUrl.value) {
    URL.revokeObjectURL(sourceImageUrl.value)
    sourceImageUrl.value = ''
  }
}

function triggerFileSelect() {
  const input = document.getElementById('background-file-input') as HTMLInputElement
  input?.click()
}

watch(
  () => props.visible,
  (visible) => {
    if (visible && props.current) {
      formData.value = { ...props.current }
      backgroundPreview.value = ''
      cropperVisible.value = false
    }
  },
)
</script>

<template>
  <a-modal
    :open="visible"
    :title="t('device.deviceDetails')"
    width="650px"
    @ok="handleOk"
    @cancel="handleClose"
  >
    <a-form :label-col="{ span: 6 }" :wrapper-col="{ span: 16 }">
      <a-form-item :label="t('device.deviceName')">
        <a-input v-model:value="formData.deviceName" class="center-input" />
      </a-form-item>

      <a-form-item :label="t('device.bindRole')">
        <a-select v-model:value="formData.roleId" class="center-select">
          <a-select-option
            v-for="role in roleItems"
            :key="role.roleId"
            :value="role.roleId"
          >
            {{ role.roleName }}
          </a-select-option>
        </a-select>
      </a-form-item>

      <!-- 背景图片上传 -->
      <a-form-item label="背景图片">
        <div class="background-upload-area">
          <!-- 裁剪编辑器 -->
          <div v-if="cropperVisible" class="cropper-container">
            <div class="cropper-canvas"
              :style="{ width: displayWidth + 'px', height: displayHeight + 'px' }">
              <!-- 原图 -->
              <img :src="sourceImageUrl" class="cropper-image"
                :style="{ width: displayWidth + 'px', height: displayHeight + 'px' }" />
              <!-- 暗色遮罩（裁剪框外部） -->
              <div class="cropper-overlay">
                <div class="overlay-top" :style="{ height: cropY + 'px' }"></div>
                <div class="overlay-middle" :style="{ height: cropH + 'px' }">
                  <div class="overlay-left" :style="{ width: cropX + 'px' }"></div>
                  <div class="crop-window"
                    :style="{ width: cropW + 'px', height: cropH + 'px' }"
                    @mousedown.prevent="onCropMouseDown">
                    <div class="crop-grid">
                      <div class="grid-line grid-h1"></div>
                      <div class="grid-line grid-h2"></div>
                      <div class="grid-line grid-v1"></div>
                      <div class="grid-line grid-v2"></div>
                    </div>
                    <div class="crop-size-label">{{ TARGET_WIDTH }}×{{ TARGET_HEIGHT }}</div>
                  </div>
                  <div class="overlay-right"></div>
                </div>
                <div class="overlay-bottom"></div>
              </div>
            </div>
            <div class="cropper-toolbar">
              <a-button size="small" @click="zoomCrop(-1)"><template #icon><ZoomOutOutlined /></template></a-button>
              <a-button size="small" @click="zoomCrop(1)"><template #icon><ZoomInOutlined /></template></a-button>
              <span class="cropper-hint">拖动裁剪框调整位置</span>
              <a-button size="small" @click="cancelCrop"><template #icon><CloseOutlined /></template> 取消</a-button>
              <a-button size="small" type="primary" :loading="backgroundUploading" @click="confirmCrop">
                <template #icon><CheckOutlined /></template> 确认并推送
              </a-button>
            </div>
          </div>

          <!-- 预览 / 占位 -->
          <template v-else>
            <div v-if="backgroundPreview" class="background-preview">
              <img :src="backgroundPreview" alt="背景预览" />
            </div>
            <div v-else class="background-placeholder">
              <PictureOutlined style="font-size: 32px; color: #999" />
              <span style="color: #999; margin-top: 8px">{{ TARGET_WIDTH }} × {{ TARGET_HEIGHT }}</span>
            </div>
          </template>

          <input
            id="background-file-input"
            type="file"
            accept="image/*"
            style="display: none"
            @change="handleFileSelect"
          />
          <a-button
            v-if="!cropperVisible"
            :loading="backgroundPushing"
            :disabled="!isDeviceOnline"
            style="margin-top: 8px"
            @click="triggerFileSelect"
          >
            <template #icon><UploadOutlined /></template>
            选择图片
          </a-button>
          <div v-if="!isDeviceOnline && !cropperVisible" class="offline-hint">
            设备离线，无法推送背景图片
          </div>
        </div>
      </a-form-item>

    </a-form>

    <template #footer>
      <a-popconfirm
        v-permission="'system:device:memory'"
        :title="t('device.confirmClearMemory')"
        :ok-text="t('common.confirm')"
        :cancel-text="t('common.cancel')"
        @confirm="handleClearMemory"
      >
        <a-button key="clear" type="primary" danger :loading="clearMemoryLoading">
          {{ t('device.clearMemory') }}
        </a-button>
      </a-popconfirm>
      <a-button key="back" @click="handleClose">{{ t('common.cancel') }}</a-button>
      <a-button v-permission="'system:device:update'" key="submit" type="primary" @click="handleOk">{{ t('common.confirm') }}</a-button>
    </template>
  </a-modal>
</template>

<style scoped lang="scss">
:deep(.center-input) {
  text-align: center;
}

:deep(.center-select .ant-select-selection-item) {
  text-align: center;
}

.background-upload-area {
  display: flex;
  flex-direction: column;
  align-items: center;
}

.background-preview {
  width: 240px;
  height: 180px;
  border: 1px solid #d9d9d9;
  border-radius: 4px;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #fafafa;

  img {
    width: 100%;
    height: 100%;
    object-fit: cover;
  }
}

.background-placeholder {
  width: 240px;
  height: 180px;
  border: 1px dashed #d9d9d9;
  border-radius: 4px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background: #fafafa;
}

.offline-hint {
  margin-top: 4px;
  font-size: 12px;
  color: #ff4d4f;
}

/* 裁剪编辑器 */
.cropper-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
}

.cropper-canvas {
  position: relative;
  border: 1px solid #d9d9d9;
  border-radius: 4px;
  overflow: hidden;
  background: #1a1a1a;
}

.cropper-image {
  display: block;
  user-select: none;
  pointer-events: none;
}

.cropper-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
}

.overlay-top {
  background: rgba(0, 0, 0, 0.55);
  flex-shrink: 0;
}

.overlay-bottom {
  flex: 1;
  background: rgba(0, 0, 0, 0.55);
}

.overlay-middle {
  display: flex;
  flex-shrink: 0;
}

.overlay-left {
  background: rgba(0, 0, 0, 0.55);
  flex-shrink: 0;
}

.overlay-right {
  background: rgba(0, 0, 0, 0.55);
  flex: 1;
}

.crop-window {
  border: 2px dashed #1890ff;
  cursor: move;
  position: relative;
  flex-shrink: 0;
  box-shadow: 0 0 0 1px rgba(24, 144, 255, 0.3);
}

.crop-grid {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

.grid-line {
  position: absolute;
  background: rgba(255, 255, 255, 0.2);
}

.grid-h1, .grid-h2 {
  left: 0;
  right: 0;
  height: 1px;
}

.grid-h1 { top: 33.33%; }
.grid-h2 { top: 66.66%; }

.grid-v1, .grid-v2 {
  top: 0;
  bottom: 0;
  width: 1px;
}

.grid-v1 { left: 33.33%; }
.grid-v2 { left: 66.66%; }

.crop-size-label {
  position: absolute;
  bottom: 4px;
  right: 6px;
  font-size: 10px;
  color: rgba(255, 255, 255, 0.7);
  background: rgba(0, 0, 0, 0.5);
  padding: 1px 5px;
  border-radius: 3px;
  pointer-events: none;
}

.cropper-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: center;
}

.cropper-hint {
  font-size: 12px;
  color: #999;
  margin: 0 4px;
}
</style>
