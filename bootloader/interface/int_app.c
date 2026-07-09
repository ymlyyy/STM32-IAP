#include "int_app.h"

/* ==================== 宏定义 ==================== */
#define BOOT_TIMEOUT_SEC             5      // 空闲等待超时（秒）
#define DATA_RECEIVE_TIMEOUT_SEC     3       // 接收数据后无新包的判定超时（秒）

/* ==================== 全局变量 ==================== */
uint32_t tick_count_ms = 0;
uint32_t tick_count_sec = 0;
uint16_t data_len = 0;          // 预期数据包总长度
uint16_t write_len_total = 0;   // 已写入 Flash 的字节数
static uint8_t last_byte = 0;   // 用于奇数长度时的最后一个字节（仅在当前文件使用）

/* ==================== 状态枚举 ==================== */
typedef enum {
    BOOT_STATE_IDLE = 0,
    BOOT_STATE_WAIT_FOR_PREDATA,
    BOOT_STATE_ERASE_FLASH,
    BOOT_STATE_WAIT_FOR_DATA,
    BOOT_STATE_JUMP_TO_APP
} BootState;

/* ==================== 环形缓冲区 ==================== */
typedef struct {
    uint8_t buffer[BOOTLOADER_UART_REC_BUFF_LEN];
    uint16_t head;
    uint16_t tail;
} RingBuffer;

RingBuffer uart_ring_buffer = { .head = 0, .tail = 0 };

static uint16_t RingBuffer_Count(RingBuffer *rb) {
    if (rb->head >= rb->tail)
        return rb->head - rb->tail;
    else
        return BOOTLOADER_UART_REC_BUFF_LEN - rb->tail + rb->head;
}

static uint8_t RingBuffer_Put(RingBuffer *rb, uint8_t data) {
    uint16_t next = (rb->head + 1) % BOOTLOADER_UART_REC_BUFF_LEN;
    if (next == rb->tail) {
        return 0;
    }
    rb->buffer[rb->head] = data;
    rb->head = next;
    return 1;
}

static void RingBuffer_PutArray(RingBuffer *rb, uint8_t *data, uint16_t len) {
    for (uint16_t i = 0; i < len; i++) {
        if (!RingBuffer_Put(rb, data[i])) {
            Error_Handler();
        }
    }
}

static uint8_t RingBuffer_Get(RingBuffer *rb, uint8_t *data) {
    if (rb->head == rb->tail) {
        return 0;
    }
    *data = rb->buffer[rb->tail];
    rb->tail = (rb->tail + 1) % BOOTLOADER_UART_REC_BUFF_LEN;
    return 1;
}

static uint8_t RingBuffer_GetHalfWord(RingBuffer *rb, uint16_t *data) {
    if (RingBuffer_Count(rb) < 2)
        return 0;

    uint8_t low, high;
    RingBuffer_Get(rb, &low);
    RingBuffer_Get(rb, &high);
    *data = ((uint16_t)high << 8) | low;
    return 1;
}

/* ==================== 辅助函数 ==================== */
static void reset_bootloader_state(void) {
    tick_count_sec = 0;
    tick_count_ms = 0;
    receive_len_total = 0;
    write_len_total = 0;
    memset(uart_receive_buff, 0, BOOTLOADER_UART_REC_BUFF_LEN);
}

__asm void boot_jump(uint32_t sp, uint32_t pc) {
    MSR msp, r0
    BX r1
}

void Int_bootloader_jump_to_app(void){
	
		typedef void (*pFunction)(void);
		
    uint32_t app_stack_prt = *(volatile uint32_t *)(APP_START_ADDRESS);
    uint32_t app_reset_handel = *(volatile uint32_t *)(APP_START_ADDRESS + 4);
    if((app_stack_prt & 0xFFFF0000)!= 0x20000000){
        printf("栈地址错误\n");
        return;
	}
	if(app_reset_handel<APP_START_ADDRESS){
        printf("复位中断地址错误\n");
        return;
    }
		
		__disable_irq();
		
		HAL_DeInit();
		
//		__set_MSP(app_stack_prt);
		
		SCB->VTOR = APP_START_ADDRESS;
		// use boot_jump instead of __set_MSP to avoid BusFault
		boot_jump(app_stack_prt, app_reset_handel);
		
		pFunction jump_to_app = (pFunction)app_reset_handel;
		
		
    jump_to_app();
}

/* ==================== 主循环状态机 ==================== */
void app_loop(void) {
    static BootState boot_state = BOOT_STATE_IDLE;

    switch (boot_state) {
        case BOOT_STATE_IDLE:
            // 倒计时提示
            if (tick_count_ms % 500 == 0) {
                printf("倒计时 %d 秒后自动跳转到应用程序...\n",
                       BOOT_TIMEOUT_SEC - tick_count_sec);
            }
            // 超时 -> 跳转
            if (tick_count_sec >= BOOT_TIMEOUT_SEC) {
                boot_state = BOOT_STATE_JUMP_TO_APP;
            }
            // 收到 UART 数据 -> 等待前置数据
            if (receive_flag == 1) {
                receive_flag = 0;
                data_len = 0;
                boot_state = BOOT_STATE_WAIT_FOR_PREDATA;
            }
            break;

        case BOOT_STATE_WAIT_FOR_PREDATA:
            // 检查头格式 "upgrade:\""
            if (uart_receive_buff[0] == 'u' && uart_receive_buff[1] == 'p' &&
                uart_receive_buff[2] == 'g' && uart_receive_buff[3] == 'r' &&
                uart_receive_buff[4] == 'a' && uart_receive_buff[5] == 'd' &&
                uart_receive_buff[6] == 'e' && uart_receive_buff[7] == ':' &&
                uart_receive_buff[8] == '"') {
                // 解析长度
                uint16_t parsed_len = 0;
                uint8_t parse_ok = 0;
                for (uint16_t i = 9; i < receive_len_total; i++) {
                    if (uart_receive_buff[i] >= '0' && uart_receive_buff[i] <= '9') {
                        parsed_len = parsed_len * 10 + (uart_receive_buff[i] - '0');
                    } else if (uart_receive_buff[i] == '"') {
                        parse_ok = 1;
                        break;
                    } else {
                        // 非法字符
                        printf("数据包长度格式不正确，返回等待状态\n");
                        reset_bootloader_state();
                        boot_state = BOOT_STATE_IDLE;
                        break;
                    }
                }
                if (parse_ok) {
                    data_len = parsed_len;
                    // 清空环形缓冲区，准备接收数据
                    uart_ring_buffer.head = 0;
                    uart_ring_buffer.tail = 0;
                    printf("已确认即将接受的数据包长度为: %d\n", data_len);
                    printf("进入擦除Flash状态...\n");
                    boot_state = BOOT_STATE_ERASE_FLASH;
                }
            } else {
                printf("数据包长度格式不正确，返回等待状态\n");
                reset_bootloader_state();
                boot_state = BOOT_STATE_IDLE;
            }
            break;

        case BOOT_STATE_ERASE_FLASH: {
            uint16_t start_page = (APP_START_ADDRESS - FLASH_BASE_ADDR) / FLASH_PAGE_SIZE;
            uint16_t end_page = (APP_START_ADDRESS - FLASH_BASE_ADDR + data_len) / FLASH_PAGE_SIZE;
            printf("擦除Flash页范围: %d - %d\n", start_page, end_page);
            printf("开始擦除Flash...\n");

            HAL_FLASH_Unlock();
            for (uint16_t page = start_page; page <= end_page; page++) {
                printf("正在擦除Flash页: %lu\n", (unsigned long)page);
                FLASH_EraseInitTypeDef erase_init;
                erase_init.TypeErase = FLASH_TYPEERASE_PAGES;
                erase_init.PageAddress = FLASH_BASE_ADDR + page * FLASH_PAGE_SIZE;
                erase_init.NbPages = 1;
                uint32_t page_error;
                if (HAL_FLASHEx_Erase(&erase_init, &page_error) != HAL_OK) {
                    printf("Flash擦除失败，页号: %lu, 错误码: %lu\n",
                           (unsigned long)page, (unsigned long)page_error);
                    Error_Handler();
                }
            }
            HAL_FLASH_Lock();

            printf("Flash擦除完成，进入接收数据状态...\n");
            memset(uart_receive_buff, 0, BOOTLOADER_UART_REC_BUFF_LEN);
            receive_len_total = 0;
            write_len_total = 0;
            HAL_FLASH_Unlock();   // 为后续写入解锁
            boot_state = BOOT_STATE_WAIT_FOR_DATA;
            break;
        }

        case BOOT_STATE_WAIT_FOR_DATA: {
            uint16_t halfword;

            // 如果有新接收的数据，放入环形缓冲区
            if (receive_flag == 1) {
                receive_flag = 0;
                printf("接收到数据包，长度: %d\n", receive_len_total);
                RingBuffer_PutArray(&uart_ring_buffer, uart_receive_buff, receive_len);
                tick_count_sec = 0;
                tick_count_ms = 0;
            } else {
                // 超时判断
                if (receive_len_total > 0 && tick_count_sec > DATA_RECEIVE_TIMEOUT_SEC) {
                    // 处理最后一个奇数字节
                    if (data_len - write_len_total == 1) {
                        if (!RingBuffer_Get(&uart_ring_buffer, &last_byte)) {
                            Error_Handler();
                        }
                        halfword = (0xFF << 8) | last_byte;  // 补齐高字节
                        if (HAL_FLASH_Program(FLASH_TYPEPROGRAM_HALFWORD,
                                              APP_START_ADDRESS + write_len_total,
                                              halfword) == HAL_OK) {
                            write_len_total += 1;
                        }
                        HAL_FLASH_Lock();
                        printf("数据写入完成，长度正确: %d\n", receive_len_total);
                        boot_state = BOOT_STATE_JUMP_TO_APP;
                        break;
                    }
                    // 正常完成
                    else if (write_len_total == data_len) {
                        HAL_FLASH_Lock();
                        printf("数据写入完成，长度正确: %d\n", receive_len_total);
                        boot_state = BOOT_STATE_JUMP_TO_APP;
                    }
                    // 长度不匹配
                    else {
                        HAL_FLASH_Lock();
                        printf("数据包接收完成，但长度不正确: %d, 期望长度: %d\n",
                               receive_len_total, data_len);
                        reset_bootloader_state();
                        boot_state = BOOT_STATE_IDLE;
                    }
                }
                // 长时间未收到任何数据
                else if (write_len_total == 0 && tick_count_sec > BOOT_TIMEOUT_SEC) {
                    HAL_FLASH_Lock();
                    printf("未接收到任何数据包，返回等待状态\n");
                    reset_bootloader_state();
                    boot_state = BOOT_STATE_IDLE;
                }
            }

            // 持续将环形缓冲区中的数据写入 Flash
            while (RingBuffer_GetHalfWord(&uart_ring_buffer, &halfword)) {
                uint32_t write_address = APP_START_ADDRESS + write_len_total;
                if (HAL_FLASH_Program(FLASH_TYPEPROGRAM_HALFWORD, write_address, halfword) == HAL_OK) {
                    write_len_total += 2;
                } else {
                    Error_Handler();
                }
            }
            break;
        }

        case BOOT_STATE_JUMP_TO_APP:
				  	printf("正在跳转至APP...\n");
            Int_bootloader_jump_to_app();
            reset_bootloader_state();
						boot_state = BOOT_STATE_IDLE;
        default:
            boot_state = BOOT_STATE_IDLE;
            break;
    }
}

/* ==================== 1ms 定时器回调 ==================== */
void My_1ms_Tick(void) {
    if (tick_count_ms++ >= 1000) {
        tick_count_ms = 0;
        tick_count_sec++;
    }
}
