#ifndef __INT_BOOTLOADER_H
#define __INT_BOOTLOADER_H

#include "usart.h"
#include "stdlib.h"
#include "string.h"

#define BOOTLOADER_UART_REC_BUFF_LEN 512
// 应用程序起始地址 0x08002800，大小为 54KB，预留 10KB 给 bootloader，总计 64KB
#define FLASH_BASE_ADDR 0x08000000
#define APP_START_ADDRESS 0x08002800  
#define APP_SIZE 0xD800        // 54KB


extern uint8_t uart_receive_buff[BOOTLOADER_UART_REC_BUFF_LEN];
extern volatile  uint8_t receive_flag;
extern volatile  uint16_t receive_len;
extern volatile  uint16_t receive_len_total;



void Int_Bootloader_init(void);

#endif /* __INT_BOOTLOADER_H */
