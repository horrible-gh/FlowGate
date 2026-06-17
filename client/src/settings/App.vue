<template>
  <RouterView />
  <ToastContainer />
</template>

<script setup>
import { RouterView } from 'vue-router';
import { ToastContainer } from '../main/components/common';
</script>
