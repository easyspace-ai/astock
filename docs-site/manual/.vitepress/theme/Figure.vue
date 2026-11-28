<script setup lang="ts">
import { computed, onUnmounted, ref } from 'vue'
import { withBase } from 'vitepress'

const props = defineProps<{ src: string; alt?: string; caption?: string }>()

// 站点可能挂在子路径下（线上是 /manual/），绝对路径必须过 withBase，
// 否则浏览器会去请求 /shots/xxx.jpg 而落到 SPA 兜底，图片全挂。
const src = computed(() => withBase(props.src))

const open = ref(false)

function show() {
  open.value = true
  document.body.style.overflow = 'hidden'
}

function hide() {
  open.value = false
  document.body.style.overflow = ''
}

onUnmounted(hide)
</script>

<template>
  <figure class="manual-figure">
    <img :src="src" :alt="props.alt || ''" loading="lazy" @click="show">
    <figcaption v-if="props.caption">{{ props.caption }}</figcaption>
    <div v-if="open" class="manual-lightbox" @click="hide">
      <img :src="src" :alt="props.alt || ''">
      <span class="manual-lightbox-tip">点击任意位置关闭</span>
    </div>
  </figure>
</template>
