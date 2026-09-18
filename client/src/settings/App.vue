<template>
  <RouterView />
  <ToastContainer />
  <ConfirmDialog host />
  <AlertDialog host />
</template>

<script setup>
import { RouterView } from 'vue-router';
import { ToastContainer } from '../main/components/common';
import AlertDialog from '../main/components/dialogs/AlertDialog.vue';
import ConfirmDialog from '../main/components/dialogs/ConfirmDialog.vue';
</script>
